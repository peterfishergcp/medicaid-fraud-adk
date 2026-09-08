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

import hashlib
import json
import logging
from typing import Any

from google.cloud import bigquery

from .config import FULL_TABLE_REF, PROJECT_ID

logger = logging.getLogger(__name__)

# Strict limits to prevent container OOM and LLM token limit exhaustion
MAX_ROW_LIMIT = 100

# Hard cap on bytes scanned per query (100 MB) to prevent Denial of Wallet / runaway queries
MAX_BYTES_BILLED = 100 * 1024 * 1024

# Module-level singleton BigQuery client to avoid re-instantiation overhead & socket exhaustion
_bq_client: bigquery.Client | None = None


def get_bq_client() -> bigquery.Client:
    """Returns the shared BigQuery client singleton."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


def _mask_password(password: Any) -> str | None:
    """Masks raw plaintext passwords into a deterministic partial hash.

    Preserves clustering/collision visibility for auditors (identical passwords produce
    identical masked hashes) while preventing plaintext credential exposure in outputs.
    Format: '***[<first 8 hex chars of sha256>]' (e.g. '***[9f86d081]').
    """
    if password is None:
        return None
    raw = str(password).strip()
    if not raw:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"***[{digest}]"


def _sanitize_rows(
    query_job: bigquery.QueryJob, max_rows: int = MAX_ROW_LIMIT
) -> list[dict[str, Any]]:
    """Converts BigQuery RowIterator to a JSON-serializable list of dicts with memory and row caps.

    Also masks plaintext passwords into deterministic partial hashes for auditor safety.

    Args:
        query_job: Executed BigQuery QueryJob.
        max_rows: Hard ceiling on rows materialized in memory.

    Returns:
        List of sanitized dictionary rows.
    """
    results: list[dict[str, Any]] = []
    for count, row in enumerate(query_job):
        if count >= max_rows:
            break
        row_dict = dict(row)
        if "PASSWORD" in row_dict and row_dict["PASSWORD"] is not None:
            row_dict["PASSWORD"] = _mask_password(row_dict["PASSWORD"])
        for k, v in row_dict.items():
            if v is not None and not isinstance(v, (str, int, float, bool)):
                row_dict[k] = str(v)
        results.append(row_dict)
    return results


def _clamp_bounds(limit: int, offset: int) -> tuple[int, int]:
    """Clamps user/LLM supplied limit and offset within safe system bounds."""
    safe_limit = min(max(1, limit), MAX_ROW_LIMIT)
    safe_offset = max(0, offset)
    return safe_limit, safe_offset


def audit_credential_recycling(
    min_cases: int = 2, limit: int = 50, offset: int = 0
) -> str:
    """Audits Medicaid applications to find credential recycling (shared USERNAME or PASSWORD across distinct NUM_CASE).

    Filters out empty strings, whitespace, and placeholder defaults to prevent false positive clusters.

    Args:
        min_cases: Minimum number of distinct cases sharing credentials to flag (default: 2, min: 2).
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the flagged application records.
    """
    safe_limit, safe_offset = _clamp_bounds(limit, offset)
    safe_min_cases = max(2, min_cases)

    client = get_bq_client()
    sql = f"""
    WITH recycled_users AS (
        SELECT LOWER(TRIM(USERNAME)) AS norm_username
        FROM `{FULL_TABLE_REF}`
        WHERE USERNAME IS NOT NULL
          AND TRIM(USERNAME) != ''
          AND LOWER(TRIM(USERNAME)) NOT IN ('n/a', 'na', 'none', 'null', 'unknown')
        GROUP BY norm_username
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    ),
    recycled_passwords AS (
        SELECT TRIM(PASSWORD) AS norm_password
        FROM `{FULL_TABLE_REF}`
        WHERE PASSWORD IS NOT NULL
          AND TRIM(PASSWORD) != ''
          AND LOWER(TRIM(PASSWORD)) NOT IN ('n/a', 'na', 'none', 'null', 'unknown')
        GROUP BY norm_password
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    )
    SELECT t.*
    FROM `{FULL_TABLE_REF}` t
    WHERE LOWER(TRIM(t.USERNAME)) IN (SELECT norm_username FROM recycled_users)
       OR TRIM(t.PASSWORD) IN (SELECT norm_password FROM recycled_passwords)
    ORDER BY t.USERNAME, t.PASSWORD
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED,
        query_parameters=[
            bigquery.ScalarQueryParameter("min_cases", "INT64", safe_min_cases),
            bigquery.ScalarQueryParameter("limit", "INT64", safe_limit),
            bigquery.ScalarQueryParameter("offset", "INT64", safe_offset),
        ],
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job, max_rows=safe_limit))
    except Exception:
        logger.error("Error executing credential_recycling audit")
        return json.dumps(
            {"error": "An error occurred while analyzing credential recycling."}
        )


def audit_address_clustering(
    min_cases: int = 3, limit: int = 50, offset: int = 0
) -> str:
    """Audits Medicaid applications to find address clustering (same ADR_STREET_1 across multiple distinct NUM_CASE).

    Normalizes whitespace and casing, and filters out empty or placeholder addresses.

    Args:
        min_cases: Minimum number of distinct cases at the same address to flag (default: 3, min: 2).
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the flagged application records.
    """
    safe_limit, safe_offset = _clamp_bounds(limit, offset)
    safe_min_cases = max(2, min_cases)

    client = get_bq_client()
    sql = f"""
    WITH clustered_addrs AS (
        SELECT LOWER(TRIM(ADR_STREET_1)) AS norm_addr
        FROM `{FULL_TABLE_REF}`
        WHERE ADR_STREET_1 IS NOT NULL
          AND TRIM(ADR_STREET_1) != ''
          AND LOWER(TRIM(ADR_STREET_1)) NOT IN ('n/a', 'na', 'none', 'null', 'unknown', 'homeless', 'po box')
        GROUP BY norm_addr
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    )
    SELECT t.*
    FROM `{FULL_TABLE_REF}` t
    WHERE LOWER(TRIM(t.ADR_STREET_1)) IN (SELECT norm_addr FROM clustered_addrs)
    ORDER BY t.ADR_STREET_1
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED,
        query_parameters=[
            bigquery.ScalarQueryParameter("min_cases", "INT64", safe_min_cases),
            bigquery.ScalarQueryParameter("limit", "INT64", safe_limit),
            bigquery.ScalarQueryParameter("offset", "INT64", safe_offset),
        ],
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job, max_rows=safe_limit))
    except Exception:
        logger.error("Error executing address_clustering audit")
        return json.dumps(
            {"error": "An error occurred while analyzing address clustering."}
        )


def audit_pregnant_members(limit: int = 50, offset: int = 0) -> str:
    """Audits Medicaid applications to find pregnant members (CDE_CAT_REL = 'CNF') sharing first names and birth years.

    Args:
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing matching records.
    """
    safe_limit, safe_offset = _clamp_bounds(limit, offset)

    client = get_bq_client()
    sql = f"""
    WITH pregnant_clusters AS (
        SELECT LOWER(TRIM(NAM_FIRST)) as norm_first, SUBSTR(CAST(DTE_BIRTH AS STRING), 1, 4) as birth_year
        FROM `{FULL_TABLE_REF}`
        WHERE CDE_CAT_REL = 'CNF'
          AND NAM_FIRST IS NOT NULL
          AND TRIM(NAM_FIRST) != ''
          AND DTE_BIRTH IS NOT NULL
        GROUP BY norm_first, birth_year
        HAVING COUNT(*) > 1
    )
    SELECT t.*
    FROM `{FULL_TABLE_REF}` t
    INNER JOIN pregnant_clusters pc
        ON LOWER(TRIM(t.NAM_FIRST)) = pc.norm_first
       AND SUBSTR(CAST(t.DTE_BIRTH AS STRING), 1, 4) = pc.birth_year
    WHERE t.CDE_CAT_REL = 'CNF'
    ORDER BY t.NAM_FIRST, t.DTE_BIRTH
    LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED,
        query_parameters=[
            bigquery.ScalarQueryParameter("limit", "INT64", safe_limit),
            bigquery.ScalarQueryParameter("offset", "INT64", safe_offset),
        ],
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job, max_rows=safe_limit))
    except Exception:
        logger.error("Error executing pregnant_members audit")
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
    password: str | None = None,
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
        password: Plaintext password string to search for (results are masked in output).
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the queried records.
    """
    safe_limit, safe_offset = _clamp_bounds(limit, offset)

    client = get_bq_client()
    sql = f"""
    SELECT *
    FROM `{FULL_TABLE_REF}`
    WHERE (@case_number IS NULL OR TRIM(NUM_CASE) = TRIM(@case_number))
      AND (@first_name IS NULL OR LOWER(TRIM(NAM_FIRST)) = LOWER(TRIM(@first_name)))
      AND (@last_name IS NULL OR LOWER(TRIM(NAM_LAST)) = LOWER(TRIM(@last_name)))
      AND (@email IS NULL OR LOWER(TRIM(EMAIL_ADDRESS)) = LOWER(TRIM(@email)))
      AND (@city IS NULL OR LOWER(TRIM(ADR_CITY)) = LOWER(TRIM(@city)))
      AND (@zip_code IS NULL OR TRIM(CAST(ADR_ZIP AS STRING)) = TRIM(@zip_code))
      AND (@password IS NULL OR TRIM(PASSWORD) = TRIM(@password))
    LIMIT @limit OFFSET @offset
    """
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter(
            "case_number", "STRING", case_number.strip() if case_number else None
        ),
        bigquery.ScalarQueryParameter(
            "first_name", "STRING", first_name.strip() if first_name else None
        ),
        bigquery.ScalarQueryParameter(
            "last_name", "STRING", last_name.strip() if last_name else None
        ),
        bigquery.ScalarQueryParameter(
            "email", "STRING", email.strip() if email else None
        ),
        bigquery.ScalarQueryParameter("city", "STRING", city.strip() if city else None),
        bigquery.ScalarQueryParameter(
            "zip_code", "STRING", str(zip_code).strip() if zip_code else None
        ),
        bigquery.ScalarQueryParameter(
            "password", "STRING", password.strip() if password else None
        ),
        bigquery.ScalarQueryParameter("limit", "INT64", safe_limit),
        bigquery.ScalarQueryParameter("offset", "INT64", safe_offset),
    ]

    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED, query_parameters=params
    )
    try:
        query_job = client.query(sql, job_config=job_config)
        return json.dumps(_sanitize_rows(query_job, max_rows=safe_limit))
    except Exception:
        logger.error("Error executing application filter")
        return json.dumps({"error": "An error occurred while querying applications."})
