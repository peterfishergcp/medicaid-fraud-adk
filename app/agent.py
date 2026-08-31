# ruff: noqa
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

import os
import google.auth

from google.adk.agents import Agent, SequentialAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools import query_syntheticdatafraud, execute_bigquery_sql

# Load environment variables (from .env or shell environment)
_, default_project = google.auth.default()
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or default_project or "your-gcp-project-id"
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DATASET_NAME = os.environ.get("BIGQUERY_DATASET", "frauddetector")
TABLE_NAME = os.environ.get("BIGQUERY_TABLE", "syntheticdatafraud")
FULL_TABLE_REF = f"{PROJECT_ID}.{DATASET_NAME}.{TABLE_NAME}"

os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# --- Stage 1: Primary Medicaid Fraud Auditor Agent ---
PRIMARY_AUDITOR_INSTRUCTION = f"""
# Role
You are the Primary Medicaid Application Auditor. Your mission is to query BigQuery (`{FULL_TABLE_REF}`) and detect suspicious or anomalous Medicaid application records.

# Core Detection Rules
1. Credential Recycling: Identical PASSWORD or USERNAME across multiple distinct NUM_CASE.
2. Address Clustering: More than 2 distinct NUM_CASE with the same ADR_STREET_1.
3. Identity Mismatch: NAM_FIRST and NAM_LAST do not match or align with USERNAME, EMAIL_ADDRESS, or password.
4. Sequential Clusters: ADR_STREET_1, EMAIL_ADDRESS, USERNAME, or PASSWORD following rapid incremental numeric sequences.
5. Pregnant members: Same NAM_FIRST and same birth year (first 4 characters of DTE_BIRTH) with CDE_CAT_REL = 'CNF'.

# Execution Guidelines
- Query the BigQuery dataset using available tools (`query_syntheticdatafraud` or `execute_bigquery_sql`).
- Compile all flagged rows into an initial structured Markdown draft.
- Keep output concise (10-15 rows per response batch if large).
"""

primary_fraud_auditor = Agent(
    name="primary_fraud_auditor",
    model=Gemini(
        model="gemini-3.7-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=PRIMARY_AUDITOR_INSTRUCTION,
    tools=[query_syntheticdatafraud, execute_bigquery_sql],
    output_key="draft_findings",
)

# --- Stage 2: Fraud Verification & Double-Check Judge Agent ---
JUDGE_INSTRUCTION = """
# Role
You are the Senior Medicaid Fraud Verification Auditor & Compliance Judge. Your job is to double-check and audit the draft findings provided by the Primary Auditor ({draft_findings}) to ensure 100% precision, zero missed records, and clear risk classification.

# Audit & Double-Check Criteria
1. Accuracy Audit: Verify that every flagged record genuinely violates one of the 5 core fraud rules (Credential Recycling, Address Clustering, Identity Mismatch, Sequential Clusters, Pregnant Members).
2. Completeness Check: Ensure no matching candidate records or edge cases were skipped.
3. Severity Classification: Assign a Severity Rating to each flagged record:
   - CRITICAL: Cross-case Credential Recycling or multi-case Address Clusters (>5 cases).
   - HIGH: Address Clusters (3-5 cases) or Pregnant Member anomalies.
   - MEDIUM: Identity Mismatch or Sequential Cluster anomalies.

# Output Expectations
Format your final output as a professional Markdown table:
NUM_CASE | ID_MEDICAID | USERNAME | PASSWORD | EMAIL_ADDRESS | PHONE_NUMBER | DTE_LAST_LOGON | NAM_FIRST | NAM_LAST | DTE_BIRTH | CDE_SEX | ADR_STREET_1 | ADR_STREET_2 | ADR_CITY | ADR_ZIP | CDE_CAT_REL | Violation Detail & Verification Summary | Risk Severity

If no records are found for a query, output: 'No violations detected for this criterion'.
If responses are batched, end with: 'Would you like me to display the next batch of findings?'
"""

fraud_verification_judge = Agent(
    name="fraud_verification_judge",
    model=Gemini(
        model="gemini-3.7-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=JUDGE_INSTRUCTION,
)

# Multi-Agent Sequential Pipeline
medicaid_fraud_pipeline = SequentialAgent(
    name="medicaid_fraud_pipeline",
    sub_agents=[primary_fraud_auditor, fraud_verification_judge],
)

root_agent = medicaid_fraud_pipeline

app = App(
    root_agent=root_agent,
    name="app",
)
