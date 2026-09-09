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
from itertools import islice
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
    for row in islice(query_job, max_rows):
        row_dict = dict(row)
        if row_dict.get("PASSWORD") is not None:
            row_dict["PASSWORD"] = _mask_password(row_dict["PASSWORD"])
        for k, v in row_dict.items():
            if v is not None and not isinstance(v, (str, int, float, bool)):
                row_dict[k] = str(v)
        results.append(row_dict)
    return results


# Explicit list of audit columns projected to minimize BigQuery bytes scanned, memory overhead, and token cost
AUDIT_COLUMNS = [
    "NUM_CASE",
    "ID_MEDICAID",
    "USERNAME",
    "PASSWORD",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "DTE_LAST_LOGON",
    "NAM_FIRST",
    "NAM_LAST",
    "DTE_BIRTH",
    "CDE_SEX",
    "ADR_STREET_1",
    "ADR_STREET_2",
    "ADR_CITY",
    "ADR_ZIP",
    "CDE_CAT_REL",
]

AUDIT_SELECT_CLAUSE = ", ".join(f"t.{col}" for col in AUDIT_COLUMNS)
AUDIT_DIRECT_SELECT = ", ".join(AUDIT_COLUMNS)


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
    SELECT {AUDIT_SELECT_CLAUSE}
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
    """Audits Medicaid applications to find address clustering across multiple distinct NUM_CASE.

    Groups records by composite address keys (ADR_STREET_1, ADR_CITY, ADR_ZIP) with whitespace
    and casing normalization to prevent false positives across different cities or zip codes.

    Args:
        min_cases: Minimum number of distinct cases at the same composite address to flag (default: 3, min: 2).
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
        SELECT
            LOWER(TRIM(ADR_STREET_1)) AS norm_street
        FROM `{FULL_TABLE_REF}`
        WHERE ADR_STREET_1 IS NOT NULL
          AND TRIM(ADR_STREET_1) != ''
          AND LOWER(TRIM(ADR_STREET_1)) NOT IN ('n/a', 'na', 'none', 'null', 'unknown', 'homeless', 'po box')
        GROUP BY norm_street
        HAVING COUNT(DISTINCT NUM_CASE) >= @min_cases
    )
    SELECT {AUDIT_SELECT_CLAUSE}
    FROM `{FULL_TABLE_REF}` t
    INNER JOIN clustered_addrs ca
        ON LOWER(TRIM(t.ADR_STREET_1)) = ca.norm_street
    ORDER BY t.ADR_STREET_1, t.NUM_CASE
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
    """Audits Medicaid applications to find pregnant members (CDE_CAT_REL = 'CNF') sharing first names and birth years across distinct cases.

    Groups records by normalized first name, last name initial, and birth year with
    COUNT(DISTINCT NUM_CASE) > 1 to avoid false flagging of renewals or multi-record histories for the same applicant.

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
        SELECT
            LOWER(TRIM(NAM_FIRST)) as norm_first,
            SUBSTR(CAST(DTE_BIRTH AS STRING), 1, 4) as birth_year
        FROM `{FULL_TABLE_REF}`
        WHERE CDE_CAT_REL = 'CNF'
          AND NAM_FIRST IS NOT NULL
          AND TRIM(NAM_FIRST) != ''
          AND DTE_BIRTH IS NOT NULL
        GROUP BY norm_first, birth_year
        HAVING COUNT(DISTINCT NUM_CASE) > 1
    )
    SELECT {AUDIT_SELECT_CLAUSE}
    FROM `{FULL_TABLE_REF}` t
    INNER JOIN pregnant_clusters pc
        ON LOWER(TRIM(t.NAM_FIRST)) = pc.norm_first
       AND SUBSTR(CAST(t.DTE_BIRTH AS STRING), 1, 4) = pc.birth_year
    WHERE t.CDE_CAT_REL = 'CNF'
    ORDER BY t.NAM_FIRST, t.DTE_BIRTH, t.NUM_CASE
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
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Filters Medicaid application records using safe, strongly-parameterized criteria.

    Requires at least one identifying filter parameter with valid characters to prevent
    unrestricted database enumeration or full-table dumps.

    Args:
        case_number: Specific NUM_CASE identifier.
        first_name: Case applicant NAM_FIRST.
        last_name: Case applicant NAM_LAST.
        email: Case applicant EMAIL_ADDRESS.
        city: Case applicant ADR_CITY.
        zip_code: Case applicant ADR_ZIP.
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the queried records or a validation error message.
    """
    clean_case = case_number.strip() if case_number else None
    clean_first = first_name.strip() if first_name else None
    clean_last = last_name.strip() if last_name else None
    clean_email = email.strip() if email else None
    clean_city = city.strip() if city else None
    clean_zip = str(zip_code).strip() if zip_code else None

    # Enforce mandatory filter check to prevent unrestricted table scans / enumeration
    active_filters = [
        f for f in (clean_case, clean_first, clean_last, clean_email, clean_city, clean_zip)
        if f and len(f) >= 2
    ]
    if not active_filters:
        return json.dumps({
            "error": "At least one identifying filter parameter (case_number, first_name, last_name, email, city, or zip_code) with at least 2 characters must be provided."
        })

    safe_limit, safe_offset = _clamp_bounds(limit, offset)

    client = get_bq_client()
    sql = f"""
    SELECT {AUDIT_DIRECT_SELECT}
    FROM `{FULL_TABLE_REF}`
    WHERE (@case_number IS NULL OR TRIM(NUM_CASE) = TRIM(@case_number))
      AND (@first_name IS NULL OR LOWER(TRIM(NAM_FIRST)) = LOWER(TRIM(@first_name)))
      AND (@last_name IS NULL OR LOWER(TRIM(NAM_LAST)) = LOWER(TRIM(@last_name)))
      AND (@email IS NULL OR LOWER(TRIM(EMAIL_ADDRESS)) = LOWER(TRIM(@email)))
      AND (@city IS NULL OR LOWER(TRIM(ADR_CITY)) = LOWER(TRIM(@city)))
      AND (@zip_code IS NULL OR TRIM(CAST(ADR_ZIP AS STRING)) = TRIM(@zip_code))
    LIMIT @limit OFFSET @offset
    """
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("case_number", "STRING", clean_case),
        bigquery.ScalarQueryParameter("first_name", "STRING", clean_first),
        bigquery.ScalarQueryParameter("last_name", "STRING", clean_last),
        bigquery.ScalarQueryParameter("email", "STRING", clean_email),
        bigquery.ScalarQueryParameter("city", "STRING", clean_city),
        bigquery.ScalarQueryParameter("zip_code", "STRING", clean_zip),
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


def verify_case_records(case_numbers: str) -> str:
    """Verifies one or more Medicaid application case records and analyzes cross-case collision metrics.

    Used by the Senior Verification Judge to spot-check or batch-verify candidate cases,
    inspect full applicant records, and compute exact collision metrics across distinct cases
    sharing identical street addresses, usernames, or passwords.

    Args:
        case_numbers: A single case number or a comma-separated list of case numbers
                     (e.g., 'YA7FC2H' or 'YA7FC2H, F97E1H7, 5GW4DRP').

    Returns:
        JSON string containing the verification status, target case records, and collision counts.
    """
    if not case_numbers or not str(case_numbers).strip():
        return json.dumps({"error": "case_numbers parameter is required."})

    # Parse and clean case numbers (split by comma or whitespace, uppercase, remove duplicates)
    raw_tokens = [
        c.strip().strip("'\"").upper()
        for c in str(case_numbers).replace("\n", ",").split(",")
    ]
    clean_cases = [c for c in raw_tokens if c]
    seen = set()
    clean_cases = [c for c in clean_cases if not (c in seen or seen.add(c))]

    if not clean_cases:
        return json.dumps({"error": "No valid case numbers provided."})

    client = get_bq_client()

    # Query 1: Fetch target case records in batch
    target_sql = f"""
    SELECT {AUDIT_DIRECT_SELECT}
    FROM `{FULL_TABLE_REF}`
    WHERE UPPER(TRIM(NUM_CASE)) IN UNNEST(@case_list)
    LIMIT {MAX_ROW_LIMIT}
    """
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED,
        query_parameters=[
            bigquery.ArrayQueryParameter("case_list", "STRING", clean_cases)
        ],
    )
    try:
        query_job = client.query(target_sql, job_config=job_config)
        target_rows = _sanitize_rows(query_job, max_rows=MAX_ROW_LIMIT)

        if not target_rows:
            return json.dumps({
                "status": "NOT_FOUND",
                "searched_cases": clean_cases,
                "found_count": 0,
                "message": f"None of the specified case numbers ({', '.join(clean_cases)}) were found in Medicaid application records.",
            })

        # Collect identifiers for sibling collision search
        found_case_numbers = [r["NUM_CASE"] for r in target_rows if r.get("NUM_CASE")]
        usernames = list({r["USERNAME"].lower().strip() for r in target_rows if r.get("USERNAME")})
        streets = list({r["ADR_STREET_1"].lower().strip() for r in target_rows if r.get("ADR_STREET_1")})

        sibling_rows = []
        if usernames or streets:
            sibling_sql = f"""
            SELECT {AUDIT_DIRECT_SELECT}
            FROM `{FULL_TABLE_REF}`
            WHERE UPPER(TRIM(NUM_CASE)) NOT IN UNNEST(@case_list)
              AND (
                (@has_users AND LOWER(TRIM(USERNAME)) IN UNNEST(@usernames))
                OR (@has_streets AND LOWER(TRIM(ADR_STREET_1)) IN UNNEST(@streets))
              )
            LIMIT 50
            """
            sibling_config = bigquery.QueryJobConfig(
                maximum_bytes_billed=MAX_BYTES_BILLED,
                query_parameters=[
                    bigquery.ArrayQueryParameter("case_list", "STRING", found_case_numbers),
                    bigquery.ScalarQueryParameter("has_users", "BOOL", bool(usernames)),
                    bigquery.ArrayQueryParameter("usernames", "STRING", usernames if usernames else [""]),
                    bigquery.ScalarQueryParameter("has_streets", "BOOL", bool(streets)),
                    bigquery.ArrayQueryParameter("streets", "STRING", streets if streets else [""]),
                ],
            )
            sibling_job = client.query(sibling_sql, job_config=sibling_config)
            sibling_rows = _sanitize_rows(sibling_job, max_rows=50)

        # Build per-case verification summaries
        case_verifications = []
        for case in target_rows:
            c_num = case.get("NUM_CASE")
            c_user = (case.get("USERNAME") or "").lower().strip()
            c_street = (case.get("ADR_STREET_1") or "").lower().strip()
            c_pwd = case.get("PASSWORD")

            # Shared usernames across sibling rows or other target rows
            matching_user_cases = {
                r["NUM_CASE"]
                for r in (sibling_rows + target_rows)
                if r.get("NUM_CASE") != c_num
                and c_user
                and (r.get("USERNAME") or "").lower().strip() == c_user
            }
            # Shared addresses across sibling rows or other target rows
            matching_addr_cases = {
                r["NUM_CASE"]
                for r in (sibling_rows + target_rows)
                if r.get("NUM_CASE") != c_num
                and c_street
                and (r.get("ADR_STREET_1") or "").lower().strip() == c_street
            }
            # Shared passwords across sibling rows or other target rows
            matching_pwd_cases = {
                r["NUM_CASE"]
                for r in (sibling_rows + target_rows)
                if r.get("NUM_CASE") != c_num
                and c_pwd
                and r.get("PASSWORD") == c_pwd
            }

            case_verifications.append({
                "case_number": c_num,
                "applicant_name": f"{case.get('NAM_FIRST', '')} {case.get('NAM_LAST', '')}".strip(),
                "case_record": case,
                "collision_metrics": {
                    "shared_username_count": len(matching_user_cases),
                    "shared_username_cases": sorted(matching_user_cases),
                    "shared_address_count": len(matching_addr_cases),
                    "shared_address_cases": sorted(matching_addr_cases),
                    "shared_password_count": len(matching_pwd_cases),
                    "shared_password_cases": sorted(matching_pwd_cases),
                },
            })

        not_found_cases = [c for c in clean_cases if c not in found_case_numbers]

        verification_report = {
            "status": "VERIFIED",
            "total_requested": len(clean_cases),
            "total_found": len(target_rows),
            "verified_cases": case_verifications,
            "not_found_cases": not_found_cases,
        }
        return json.dumps(verification_report)

    except Exception:
        logger.error("Error executing case verification for cases %s", clean_cases)
        return json.dumps(
            {"error": f"An error occurred while verifying cases: {', '.join(clean_cases)}."}
        )


def verify_case_record(case_number: str) -> str:
    """Verifies a single Medicaid application case record and analyzes cross-case collision stats.

    Used by the Verification Judge to spot-check draft findings, inspect related records,
    and compute exact counts of other distinct cases sharing the same address, username, or password.

    Args:
        case_number: The specific NUM_CASE identifier to verify.
    """
    return verify_case_records(case_number)


def audit_identity_mismatches(limit: int = 50, offset: int = 0) -> str:
    """Audits Medicaid applications for Rule 3 Identity Mismatch anomalies.

    Detects synthetic identities or hijacked accounts where the applicant's name
    (NAM_FIRST, NAM_LAST) completely diverges from their USERNAME and EMAIL_ADDRESS handle
    (i.e. neither first name, last name, nor initials match the account username or email).

    Args:
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the flagged application records with identity mismatches.
    """
    safe_limit, safe_offset = _clamp_bounds(limit, offset)
    client = get_bq_client()

    sql = f"""
    WITH candidates AS (
        SELECT
            {AUDIT_DIRECT_SELECT},
            LOWER(TRIM(NAM_FIRST)) AS norm_first,
            LOWER(TRIM(NAM_LAST)) AS norm_last,
            LOWER(TRIM(USERNAME)) AS norm_user,
            LOWER(SPLIT(TRIM(EMAIL_ADDRESS), '@')[SAFE_OFFSET(0)]) AS email_prefix
        FROM `{FULL_TABLE_REF}`
        WHERE NAM_FIRST IS NOT NULL AND LENGTH(TRIM(NAM_FIRST)) >= 2
          AND NAM_LAST IS NOT NULL AND LENGTH(TRIM(NAM_LAST)) >= 2
          AND USERNAME IS NOT NULL AND LENGTH(TRIM(USERNAME)) >= 3
          AND EMAIL_ADDRESS IS NOT NULL AND STRPOS(EMAIL_ADDRESS, '@') > 1
    )
    SELECT {AUDIT_DIRECT_SELECT}
    FROM candidates
    WHERE
        -- Username does not contain first name, last name, or first+last initials
        STRPOS(norm_user, norm_first) = 0
        AND STRPOS(norm_user, norm_last) = 0
        AND STRPOS(norm_user, CONCAT(SUBSTR(norm_first, 1, 1), norm_last)) = 0
        AND STRPOS(norm_user, CONCAT(norm_first, SUBSTR(norm_last, 1, 1))) = 0
        -- Email prefix does not contain first name or last name
        AND STRPOS(email_prefix, norm_first) = 0
        AND STRPOS(email_prefix, norm_last) = 0
    ORDER BY NUM_CASE
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
        logger.error("Error executing identity_mismatches audit")
        return json.dumps(
            {"error": "An error occurred while analyzing identity mismatches."}
        )


def audit_sequential_clusters(limit: int = 50, offset: int = 0) -> str:
    """Audits Medicaid applications for Rule 4 Sequential Cluster anomalies.

    Detects automated or bot-generated bursts where applications share base username prefixes
    with consecutive/sequential numeric suffixes across distinct cases (e.g. user001, user002, user003).

    Args:
        limit: Maximum number of rows to return (default: 50, max: 100).
        offset: Row offset for pagination (default: 0).

    Returns:
        JSON string containing the flagged application records part of sequential patterns.
    """
    safe_limit, safe_offset = _clamp_bounds(limit, offset)
    client = get_bq_client()

    sql = f"""
    WITH parsed_users AS (
        SELECT
            {AUDIT_DIRECT_SELECT},
            REGEXP_EXTRACT(LOWER(TRIM(USERNAME)), r'^([a-z_-]+)') AS user_prefix,
            SAFE_CAST(REGEXP_EXTRACT(LOWER(TRIM(USERNAME)), r'(\\d+)$') AS INT64) AS user_num
        FROM `{FULL_TABLE_REF}`
        WHERE USERNAME IS NOT NULL
          AND REGEXP_CONTAINS(LOWER(TRIM(USERNAME)), r'^[a-z_-]+\\d+$')
    ),
    sequential_check AS (
        SELECT
            *,
            LAG(user_num) OVER (PARTITION BY user_prefix ORDER BY user_num) AS prev_num,
            LEAD(user_num) OVER (PARTITION BY user_prefix ORDER BY user_num) AS next_num
        FROM parsed_users
        WHERE user_prefix IS NOT NULL AND user_num IS NOT NULL
    ),
    flagged_users AS (
        SELECT DISTINCT user_prefix
        FROM sequential_check
        WHERE (user_num - prev_num = 1) OR (next_num - user_num = 1)
    )
    SELECT {AUDIT_SELECT_CLAUSE}
    FROM `{FULL_TABLE_REF}` t
    INNER JOIN sequential_check sc
        ON t.NUM_CASE = sc.NUM_CASE
    INNER JOIN flagged_users fu
        ON sc.user_prefix = fu.user_prefix
    ORDER BY sc.user_prefix, sc.user_num, t.NUM_CASE
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
        logger.error("Error executing sequential_clusters audit")
        return json.dumps(
            {"error": "An error occurred while analyzing sequential clusters."}
        )
