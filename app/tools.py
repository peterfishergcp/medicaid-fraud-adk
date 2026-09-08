# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import re
from typing import Any

from google.cloud import bigquery

from app.config import FULL_TABLE_REF, PROJECT_ID

logger = logging.getLogger(__name__)


def _sanitize_rows(query_job: bigquery.QueryJob) -> list[dict[str, Any]]:
    """Helper to convert BigQuery RowIterator to a JSON-serializable list of dicts."""
    results = [dict(row) for row in query_job]
    for r in results:
        for k, v in r.items():
            if v is not None and not isinstance(v, (str, int, float, bool)):
                r[k] = str(v)
    return results


def audit_credential_recycling(
    min_cases: int = 2, limit: int = 50, offset: int = 0
) -> str:
    """Audits Medicaid applications to find credential recycling (shared USERNAME or PASSWORD across distinct NUM_CASE).

    Args:
        min_cases: Minimum number of distinct cases sharing credentials to flag (default: 2).
        limit: Maximum number of rows to return (default: 50).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the flagged application records.
    """
    client = bigquery.Client(project=PROJECT_ID)
    sql = f"""
    WITH recycled_users AS (
        SELECT USERNAME
        FROM `{FULL_TABLE_REF}`
        WHERE USERNAME IS NOT NULL
        GROUP BY USERNAME
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    ),
    recycled_passwords AS (
        SELECT PASSWORD
        FROM `{FULL_TABLE_REF}`
        WHERE PASSWORD IS NOT NULL
        GROUP BY PASSWORD
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    )
    SELECT t.*
    FROM `{FULL_TABLE_REF}` t
    WHERE t.USERNAME IN (SELECT USERNAME FROM recycled_users)
       OR t.PASSWORD IN (SELECT PASSWORD FROM recycled_passwords)
    ORDER BY t.USERNAME, t.PASSWORD
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("min_cases", "INT64", min_cases),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
        ]
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job))
    except Exception as e:
        logger.error("Error running audit_credential_recycling: %s", e)
        return json.dumps(
            {"error": "An error occurred while analyzing credential recycling."}
        )


def audit_address_clustering(
    min_cases: int = 3, limit: int = 50, offset: int = 0
) -> str:
    """Audits Medicaid applications to find address clustering (same ADR_STREET_1 across multiple distinct NUM_CASE).

    Args:
        min_cases: Minimum number of distinct cases at the same address to flag (default: 3).
        limit: Maximum number of rows to return (default: 50).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the flagged application records.
    """
    client = bigquery.Client(project=PROJECT_ID)
    sql = f"""
    WITH clustered_addrs AS (
        SELECT ADR_STREET_1
        FROM `{FULL_TABLE_REF}`
        WHERE ADR_STREET_1 IS NOT NULL
        GROUP BY ADR_STREET_1
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    )
    SELECT t.*
    FROM `{FULL_TABLE_REF}` t
    WHERE t.ADR_STREET_1 IN (SELECT ADR_STREET_1 FROM clustered_addrs)
    ORDER BY t.ADR_STREET_1
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("min_cases", "INT64", min_cases),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
        ]
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job))
    except Exception as e:
        logger.error("Error running audit_address_clustering: %s", e)
        return json.dumps(
            {"error": "An error occurred while analyzing address clustering."}
        )


def audit_pregnant_members(limit: int = 50, offset: int = 0) -> str:
    """Audits Medicaid applications to find pregnant members (CDE_CAT_REL = 'CNF') sharing first names and birth years.

    Args:
        limit: Maximum number of rows to return (default: 50).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing matching records.
    """
    client = bigquery.Client(project=PROJECT_ID)
    sql = f"""
    WITH pregnant_clusters AS (
        SELECT NAM_FIRST, SUBSTR(CAST(DTE_BIRTH AS STRING), 1, 4) as birth_year
        FROM `{FULL_TABLE_REF}`
        WHERE CDE_CAT_REL = 'CNF' AND NAM_FIRST IS NOT NULL AND DTE_BIRTH IS NOT NULL
        GROUP BY NAM_FIRST, birth_year
        HAVING COUNT(*) > 1
    )
    SELECT t.*
    FROM `{FULL_TABLE_REF}` t
    INNER JOIN pregnant_clusters pc
        ON t.NAM_FIRST = pc.NAM_FIRST
       AND SUBSTR(CAST(t.DTE_BIRTH AS STRING), 1, 4) = pc.birth_year
    WHERE t.CDE_CAT_REL = 'CNF'
    ORDER BY t.NAM_FIRST, t.DTE_BIRTH
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
        ]
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job))
    except Exception as e:
        logger.error("Error running audit_pregnant_members: %s", e)
        return json.dumps(
            {"error": "An error occurred while analyzing pregnant member records."}
        )


def filter_applications(
    case_number: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    city: str | None = None,
    zip_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Filters Medicaid application records using safe, strongly-parameterized criteria.

    Args:
        case_number: Specific NUM_CASE identifier.
        first_name: Case applicant NAM_FIRST.
        last_name: Case applicant NAM_LAST.
        email: Case applicant EMAIL_ADDRESS.
        city: Case applicant ADR_CITY.
        zip_code: Case applicant ADR_ZIP.
        limit: Maximum number of rows to return (default: 50).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the queried records.
    """
    client = bigquery.Client(project=PROJECT_ID)
    conditions = []
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
        bigquery.ScalarQueryParameter("offset", "INT64", offset),
    ]

    if case_number:
        conditions.append("NUM_CASE = @case_number")
        params.append(
            bigquery.ScalarQueryParameter("case_number", "STRING", case_number)
        )
    if first_name:
        conditions.append("LOWER(NAM_FIRST) = LOWER(@first_name)")
        params.append(bigquery.ScalarQueryParameter("first_name", "STRING", first_name))
    if last_name:
        conditions.append("LOWER(NAM_LAST) = LOWER(@last_name)")
        params.append(bigquery.ScalarQueryParameter("last_name", "STRING", last_name))
    if email:
        conditions.append("LOWER(EMAIL_ADDRESS) = LOWER(@email)")
        params.append(bigquery.ScalarQueryParameter("email", "STRING", email))
    if city:
        conditions.append("LOWER(ADR_CITY) = LOWER(@city)")
        params.append(bigquery.ScalarQueryParameter("city", "STRING", city))
    if zip_code:
        conditions.append("ADR_ZIP = @zip_code")
        params.append(bigquery.ScalarQueryParameter("zip_code", "STRING", zip_code))

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
    SELECT *
    FROM `{FULL_TABLE_REF}`
    WHERE {where_clause}
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job))
    except Exception as e:
        logger.error("Error running filter_applications: %s", e)
        return json.dumps({"error": "An error occurred while querying applications."})


# Regex to disallow destructive or non-SELECT DDL/DML operations
FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|MERGE|TRUNCATE|GRANT|REVOKE|EXECUTE|EXEC|CALL)\b",
    re.IGNORECASE,
)


def execute_read_only_bigquery_sql(sql_query: str) -> str:
    """Executes a strictly read-only SELECT query in BigQuery for custom analytics and fraud investigations.

    Args:
        sql_query: A valid BigQuery Standard SQL SELECT statement.

    Returns:
        JSON string containing the query results or error message.
    """
    stripped_query = sql_query.strip()
    # 1. Enforce SELECT only
    if not stripped_query.lower().startswith(
        "select"
    ) and not stripped_query.lower().startswith("with"):
        return json.dumps(
            {"error": "Only read-only SELECT or WITH statements are permitted."}
        )

    # 2. Block DML / DDL / Administrative SQL
    if FORBIDDEN_SQL_KEYWORDS.search(stripped_query):
        return json.dumps(
            {
                "error": "Disallowed SQL statement detected. Only read-only operations are supported."
            }
        )

    client = bigquery.Client(project=PROJECT_ID)
    try:
        query_job = client.query(stripped_query)
        return json.dumps(_sanitize_rows(query_job))
    except Exception as e:
        logger.error("Error executing read-only query: %s", e)
        return json.dumps({"error": "An error occurred while executing the SQL query."})
