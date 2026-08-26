# DESIGN_SPEC.md: Medicaid Application Auditor ADK Agent

## Overview

The **Medicaid Application Auditor** is an autonomous ADK agent designed to analyze Medicaid application data extracts in BigQuery (`ai-hub-459714.frauddector.syntheticdatafraud`) and identify highly suspicious, anomalous, or potentially fraudulent submissions.

The agent leverages a remote/built-in **BigQuery MCP Server** (`https://bigquery.googleapis.com/mcp`) to query and evaluate application records for both individual row anomalies and cross-row clustering patterns.

The project packages the ADK Agent along with the **ADK Web UI** (`adk web`), containerized via Docker and deployed to **Google Cloud Run** in project `ai-hub-459714`.

---

## Agent Configuration

- **Agent Name**: `medicaid_application_auditor`
- **Model**: `gemini-3.7-flash` (Gemini 3.7 Flash)
- **Target BigQuery Table**: `ai-hub-459714.frauddector.syntheticdatafraud`
- **MCP Server Connector**: BigQuery Remote MCP Server (`https://bigquery.googleapis.com/mcp`) / `execute_sql`

---

## Core Detection Rules & Business Logic

The agent evaluates BigQuery records against 5 specific fraud/anomaly rules:

1. **Credential Recycling**:
   - Use of identical `PASSWORD` or `USERNAME` across multiple distinct `NUM_CASE`.
2. **Address Clustering**:
   - More than 2 distinct `NUM_CASE` associated with the exact same `ADR_STREET_1`.
3. **Identity Mismatch**:
   - `NAM_FIRST` and `NAM_LAST` do not match or align with their `USERNAME`, `EMAIL_ADDRESS` prefix, or password strings.
4. **Sequential Clusters**:
   - `ADR_STREET_1`, `EMAIL_ADDRESS`, `USERNAME`, or `PASSWORD` values that follow rapid incremental or sequential numeric/alphanumeric patterns.
5. **Pregnant Members Pattern**:
   - Same `NAM_FIRST` and same first 4 characters of birth date (`DTE_BIRTH` YYYY) with coverage category relationship `CDE_CAT_REL = 'CNF'`.

---

## Output Expectations & Constraints

### Markdown Table Schema
All findings are formatted as a structured Markdown table with the following exact columns:
`NUM_CASE | ID_MEDICAID | USERNAME | PASSWORD | EMAIL_ADDRESS | PHONE_NUMBER | DTE_LAST_LOGON | NAM_FIRST | NAM_LAST | DTE_BIRTH | CDE_SEX | ADR_STREET_1 | ADR_STREET_2 | ADR_CITY | ADR_ZIP | CDE_CAT_REL | Detailed explanation of what exactly matched`

### Operational Rules
- **Batching**: Results are displayed in manageable batches of 10–15 rows at a time to prevent response truncation. Batched responses end with: *"Would you like me to display the next batch of findings?"*
- **Targeted Query Execution**: When asked about a specific rule or scenario, analysis is strictly isolated to that specific rule for maximum performance.
- **Empty Results Fallback**: If no violations are found for a requested query/rule, output cleanly: *"No violations detected for this criterion."*

---

## Architecture & Infrastructure

- **Agent Framework**: Google Agent Development Kit (ADK) in Python (`google-adk`).
- **Web UI**: ADK Web Playground (`adk web`).
- **Deployment**: Google Cloud Run on GCP Project `ai-hub-459714` (Region: `us-central1`).
- **Repository**: `peterfishergcp/medicaid-fraud-detector-adk` on GitHub.
