import json
import os
from typing import Any, Dict, List, Optional
import google.auth
from google.cloud import bigquery

# Dynamic project & BigQuery target table setup
_, default_project = google.auth.default()
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or default_project or "your-gcp-project-id"
DATASET_NAME = os.environ.get("BIGQUERY_DATASET", "frauddetector")
TABLE_NAME = os.environ.get("BIGQUERY_TABLE", "syntheticdatafraud")
TARGET_TABLE = f"{PROJECT_ID}.{DATASET_NAME}.{TABLE_NAME}"


def query_syntheticdatafraud(
    query_type: str = "all",
    limit: int = 50,
    offset: int = 0,
    custom_where_clause: Optional[str] = None
) -> str:
    """Queries the Medicaid application dataset in BigQuery.

    Args:
        query_type: Type of query analysis to run. Supported types:
            - 'all': Fetch raw rows from target dataset.
            - 'credential_recycling': Find duplicate USERNAME or PASSWORD across distinct NUM_CASE.
            - 'address_clustering': Find ADR_STREET_1 values with more than 2 distinct NUM_CASE.
            - 'pregnant_members': Find members with CDE_CAT_REL = 'CNF' sharing NAM_FIRST and birth year (first 4 chars of DTE_BIRTH).
            - 'custom': Custom filter supplied via custom_where_clause.
        limit: Maximum number of rows to return (default: 50).
        offset: Row offset for pagination (default: 0).
        custom_where_clause: Optional SQL WHERE clause when query_type is 'custom'.

    Returns:
        JSON string containing the queried records or analytical matches.
    """
    client = bigquery.Client(project=PROJECT_ID)

    if query_type == "credential_recycling":
        sql = f"""
        WITH recycled_users AS (
            SELECT USERNAME FROM `{TARGET_TABLE}` WHERE USERNAME IS NOT NULL GROUP BY USERNAME HAVING COUNT(DISTINCT NUM_CASE) > 1
        ),
        recycled_passwords AS (
            SELECT PASSWORD FROM `{TARGET_TABLE}` WHERE PASSWORD IS NOT NULL GROUP BY PASSWORD HAVING COUNT(DISTINCT NUM_CASE) > 1
        )
        SELECT t.*
        FROM `{TARGET_TABLE}` t
        WHERE t.USERNAME IN (SELECT USERNAME FROM recycled_users)
           OR t.PASSWORD IN (SELECT PASSWORD FROM recycled_passwords)
        ORDER BY t.USERNAME, t.PASSWORD
        LIMIT {limit} OFFSET {offset}
        """
    elif query_type == "address_clustering":
        sql = f"""
        WITH clustered_addrs AS (
            SELECT ADR_STREET_1
            FROM `{TARGET_TABLE}`
            WHERE ADR_STREET_1 IS NOT NULL
            GROUP BY ADR_STREET_1
            HAVING COUNT(DISTINCT NUM_CASE) > 2
        )
        SELECT t.*
        FROM `{TARGET_TABLE}` t
        WHERE t.ADR_STREET_1 IN (SELECT ADR_STREET_1 FROM clustered_addrs)
        ORDER BY t.ADR_STREET_1
        LIMIT {limit} OFFSET {offset}
        """
    elif query_type == "pregnant_members":
        sql = f"""
        WITH pregnant_clusters AS (
            SELECT NAM_FIRST, SUBSTR(CAST(DTE_BIRTH AS STRING), 1, 4) as birth_year
            FROM `{TARGET_TABLE}`
            WHERE CDE_CAT_REL = 'CNF' AND NAM_FIRST IS NOT NULL AND DTE_BIRTH IS NOT NULL
            GROUP BY NAM_FIRST, birth_year
            HAVING COUNT(*) > 1
        )
        SELECT t.*
        FROM `{TARGET_TABLE}` t
        INNER JOIN pregnant_clusters pc
            ON t.NAM_FIRST = pc.NAM_FIRST
           AND SUBSTR(CAST(t.DTE_BIRTH AS STRING), 1, 4) = pc.birth_year
        WHERE t.CDE_CAT_REL = 'CNF'
        ORDER BY t.NAM_FIRST, t.DTE_BIRTH
        LIMIT {limit} OFFSET {offset}
        """
    elif query_type == "custom" and custom_where_clause:
        sql = f"""
        SELECT *
        FROM `{TARGET_TABLE}`
        WHERE {custom_where_clause}
        LIMIT {limit} OFFSET {offset}
        """
    else:
        sql = f"""
        SELECT *
        FROM `{TARGET_TABLE}`
        LIMIT {limit} OFFSET {offset}
        """

    try:
        query_job = client.query(sql)
        results = [dict(row) for row in query_job]
        # Convert non-serializable objects (like dates/integers)
        for r in results:
            for k, v in r.items():
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    r[k] = str(v)
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": str(e)})


def execute_bigquery_sql(sql_query: str) -> str:
    """Executes an arbitrary BigQuery SQL query against the dataset.

    Args:
        sql_query: Valid BigQuery Standard SQL string.

    Returns:
        JSON string containing the query results.
    """
    client = bigquery.Client(project=PROJECT_ID)
    try:
        query_job = client.query(sql_query)
        results = [dict(row) for row in query_job]
        for r in results:
            for k, v in r.items():
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    r[k] = str(v)
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": str(e)})
