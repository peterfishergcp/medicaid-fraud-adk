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
import os

from app.agent import (
    GlobalGemini,
    fraud_verification_judge,
    primary_fraud_auditor,
    redact_sensitive_infrastructure,
)
from app.app_utils.reasoning_engine_adapter import _no_op_instrumentor_builder
from app.app_utils.telemetry import setup_telemetry
from app.config import LOCATION
from app.tools import (
    AUDIT_COLUMNS,
    _clamp_bounds,
    _mask_password,
    audit_address_clustering,
    audit_credential_recycling,
    audit_identity_mismatches,
    audit_pregnant_members,
    audit_sequential_clusters,
    filter_applications,
    verify_case_record,
    verify_case_records,
)
from scripts.publish_ge import BASE_DISPLAY_NAME, format_versioned_display_name


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
    sample_text = (
        "Querying table `ai-hub-459714.frauddector.syntheticdatafraud` for results."
    )
    scrubbed = redact_sensitive_infrastructure(sample_text)
    assert "ai-hub-459714" not in scrubbed
    assert "frauddector" not in scrubbed
    assert "syntheticdatafraud" not in scrubbed
    assert "[REDACTED_SYSTEM_INFO]" in scrubbed


def test_verify_case_record_empty_input() -> None:
    """Tests verify_case_record error response on empty/whitespace input."""
    res = json.loads(verify_case_record(""))
    assert "error" in res

    res = json.loads(verify_case_record("   "))
    assert "error" in res

    res_batch = json.loads(verify_case_records(""))
    assert "error" in res_batch

    res_batch = json.loads(verify_case_records("   ,  ,, "))
    assert "error" in res_batch


def test_audit_tools_registered() -> None:
    """Verifies all fraud tools and verification tools are registered."""
    registered_tools = primary_fraud_auditor.tools
    assert audit_credential_recycling in registered_tools
    assert audit_address_clustering in registered_tools
    assert audit_pregnant_members in registered_tools
    assert audit_identity_mismatches in registered_tools
    assert audit_sequential_clusters in registered_tools
    assert filter_applications in registered_tools

    judge_tools = fraud_verification_judge.tools
    assert verify_case_records in judge_tools
    assert verify_case_record in judge_tools
    assert filter_applications in judge_tools


def test_filter_applications_mandatory_criteria() -> None:
    """Verifies that filter_applications rejects empty/unrestricted queries."""
    res = json.loads(filter_applications())
    assert "error" in res
    assert "At least one identifying filter parameter" in res["error"]

    res = json.loads(filter_applications(case_number=" ", first_name="a"))
    assert "error" in res


def test_format_versioned_display_name() -> None:
    """Verifies display name version formatting puts version at the beginning."""
    v2_name = format_versioned_display_name(BASE_DISPLAY_NAME, "v2")
    assert v2_name == "v2 - Medicaid Application Auditor"

    bracket_name = format_versioned_display_name(BASE_DISPLAY_NAME, "[v2]")
    assert bracket_name == "[v2] Medicaid Application Auditor"

    v12_name = format_versioned_display_name(BASE_DISPLAY_NAME, "v1.2")
    assert v12_name == "v1.2 - Medicaid Application Auditor"

    empty_name = format_versioned_display_name(BASE_DISPLAY_NAME, "")
    assert empty_name == "Medicaid Application Auditor"

    whitespace_name = format_versioned_display_name(BASE_DISPLAY_NAME, "   ")
    assert whitespace_name == "Medicaid Application Auditor"


def test_global_gemini_model_location_pinning() -> None:
    """Verifies Gemini 3.8 Flash agents are pinned to the global Vertex AI endpoint."""
    assert LOCATION == "global"
    assert isinstance(primary_fraud_auditor.model, GlobalGemini)
    assert primary_fraud_auditor.model.model == "gemini-3.8-flash"
    assert primary_fraud_auditor.model.location == "global"

    assert isinstance(fraud_verification_judge.model, GlobalGemini)
    assert fraud_verification_judge.model.model == "gemini-3.8-flash"
    assert fraud_verification_judge.model.location == "global"


def test_telemetry_and_no_op_instrumentor() -> None:
    """Verifies telemetry configuration and no-op AdkApp instrumentor builder."""
    assert _no_op_instrumentor_builder("test-project") is None
    setup_telemetry()
    assert os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY") == "true"
    assert os.environ.get("OTEL_SERVICE_NAME") == "medicaid-fraud-adk"
