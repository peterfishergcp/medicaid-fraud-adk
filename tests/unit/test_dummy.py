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
import pytest
from google.genai import types

from app.agent import redact_sensitive_infrastructure
from app.tools import _mask_password, _clamp_bounds, AUDIT_COLUMNS


def test_mask_password() -> None:
    """Tests deterministic partial hashing password masking."""
    assert _mask_password("") == ""
    assert _mask_password(None) is None

    pwd = "secretpassword123"
    masked = _mask_password(pwd)
    expected_hash = hashlib.sha256(pwd.encode("utf-8")).hexdigest()[:8]
    assert masked == f"***[{expected_hash}]"

    # Determinism: same password produces same masked token
    assert _mask_password("secretpassword123") == _mask_password("secretpassword123")
    # Different passwords produce different masked tokens
    assert _mask_password("passwordA") != _mask_password("passwordB")


def test_clamp_bounds() -> None:
    """Tests limit and offset clamping."""
    limit, offset = _clamp_bounds(-5, -10)
    assert limit == 1
    assert offset == 0

    limit, offset = _clamp_bounds(500, 20)
    assert limit == 100
    assert offset == 20


def test_audit_columns_completeness() -> None:
    """Ensures standard audit columns list contains all required fields."""
    required = {
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
    }
    assert required.issubset(set(AUDIT_COLUMNS))


def test_redact_sensitive_infrastructure() -> None:
    """Tests infrastructure scrubbing for project IDs, tables, and datasets."""
    sample_text = "Querying table `ai-hub-459714.frauddector.syntheticdatafraud` for results."
    scrubbed = redact_sensitive_infrastructure(sample_text)
    assert "ai-hub-459714" not in scrubbed
    assert "frauddector" not in scrubbed
    assert "syntheticdatafraud" not in scrubbed
    assert "[REDACTED_SYSTEM_INFO]" in scrubbed


def test_verify_case_record_empty_input() -> None:
    """Tests verify_case_record error response on empty/whitespace input."""
    import json
    from app.tools import verify_case_record

    res = json.loads(verify_case_record(""))
    assert "error" in res

    res = json.loads(verify_case_record("   "))
    assert "error" in res
