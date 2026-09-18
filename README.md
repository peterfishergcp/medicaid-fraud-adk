# Medicaid Application Auditor ADK Agent

> **DISCLAIMER:** This project is provided solely as an illustrative sample and proof-of-concept for educational and demonstration purposes. This is **NOT** an official Google product or officially supported Google software. It is provided "as is" without warranty or guarantee of any kind.

Enterprise two-stage autonomous fraud detection and compliance auditing agent built with the **Google Agent Development Kit (ADK)** and powered by **Gemini 3.8 Flash (`gemini-3.8-flash`)**.

### Dual-Stage `SequentialAgent` Pipeline
1. **Stage 1 (`primary_fraud_auditor`)**: Executes parameterized, read-only BigQuery queries across 5 core Medicaid fraud patterns (Credential Recycling, Address Clustering, Identity Mismatches, Sequential Account Generation, and Pregnant Member Anomalies) and writes candidate cases to session state (`draft_findings`).
2. **Stage 2 (`fraud_verification_judge`)**: Independently cross-verifies candidate cases (`verify_case_records`, `verify_case_record`), eliminates false positives, assigns objective risk severities (`CRITICAL`, `HIGH`, `MEDIUM`), and formats a 7-column compliance dossier.
3. **Deterministic Security Redaction (`sanitize_agent_output`)**: An automated `after_agent_callback` deterministically strips internal GCP project IDs, dataset names, and table references before responses leave the backend.

---

## Project Structure

```text
medicaid-fraud-adk/
├── app/                       # Core ADK agent code
│   ├── agent.py               # Dual-stage SequentialAgent pipeline & redaction callbacks
│   ├── config.py              # Dynamic environment & BigQuery table configuration
│   ├── tools.py               # Parameterized BigQuery fraud detection & verification tools
│   ├── fast_api_app.py        # FastAPI & Agent Engine HTTP server
│   └── app_utils/             # Reasoning Engine adapter, telemetry, and typing utilities
├── scripts/
│   ├── deploy_and_publish.py  # Automated 1-step Agent Engine deploy & Gemini Enterprise publisher
│   └── publish_ge.py          # Versioned Gemini Enterprise agent registration utility
├── tests/                     # Unit, integration, and ADK evaluation suites
├── install.sh                 # Interactive setup & .env configuration script
├── Makefile                   # Development, testing, and deployment targets
└── pyproject.toml             # Python dependencies (managed via uv)
```

---

## Prerequisites

Before you begin, ensure you have:
- **uv**: Python package manager ([Install](https://docs.astral.sh/uv/getting-started/installation/))
- **Google Cloud SDK (`gcloud`)**: Authenticated (`gcloud auth login` and `gcloud auth application-default login`)
- **BigQuery Dataset & Table**: A BigQuery table containing Medicaid application records (default: `<project-id>.frauddector.syntheticdatafraud`)

---

## Quick Start (Interactive Setup)

1. **Run the interactive installer** to configure your `.env` (GCP Project ID, Region, BigQuery Dataset/Table, and optional Gemini Enterprise App ID) and install dependencies:

   ```bash
   ./install.sh
   ```

2. **Launch the local ADK Web Playground**:

   ```bash
   make playground
   ```

### Environment Configuration (`.env`)

`./install.sh` generates a `.env` file in the project root with the following variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `GOOGLE_CLOUD_PROJECT` | Target GCP Project ID | Active `gcloud` project |
| `GOOGLE_CLOUD_LOCATION` | GCP Region / Location | `us-central1` |
| `BIGQUERY_DATASET` | BigQuery Dataset containing Medicaid records | `frauddector` |
| `BIGQUERY_TABLE` | BigQuery Table name within the dataset | `syntheticdatafraud` |
| `GEMINI_ENTERPRISE_APPS` | Comma-separated Gemini Enterprise Engine IDs or full resource URIs | *(Optional)* |

---

## Deployment Options

### Option 1: Vertex AI Agent Engine + Gemini Enterprise (Recommended)

Deploy the agent to **Vertex AI Agent Engine** (automatically injecting your `BIGQUERY_DATASET` and `BIGQUERY_TABLE` environment variables into the remote runtime) and register it into **Gemini Enterprise** in a single command:

```bash
make deploy-ge
```

Or run the deployment script directly with custom CLI overrides:

```bash
uv run python scripts/deploy_and_publish.py \
  --project <your-project-id> \
  --dataset <your-bq-dataset> \
  --table <your-bq-table> \
  --apps <your-gemini-enterprise-engine-id>
```

> **Note on BigQuery IAM Permissions:** `deploy_and_publish.py` automatically checks and binds `roles/bigquery.dataViewer` and `roles/bigquery.jobUser` to your project's Vertex AI Reasoning Engine Service Account (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) so the deployed agent can query your BigQuery dataset.

### Option 2: Cloud Run Deployment

Deploy the agent as a containerized FastAPI service on Cloud Run (automatically forwarding your `.env` settings):

```bash
make deploy
```

---

## Sample Prompts to Test

Once running in `make playground` or inside **Gemini Enterprise**, try these test queries:

1. **Comprehensive Multi-Rule Fraud Audit**:
   > *"Run a comprehensive fraud audit across credential recycling, residential address clustering, and pregnant member anomalies. Verify the top flagged cases and generate a full 7-column compliance dossier with severity ratings and investigative next steps."*

2. **Targeted Credential & Address Mill Audit**:
   > *"Audit the Medicaid applications for credential recycling and shared residential address mills, and show me the verified CRITICAL and HIGH risk collisions."*

3. **Specific Case Forensic Deep-Dive**:
   > *"Investigate case number 44POG5Z (David Williams) and verify all sibling cases sharing the same credentials or address."*

4. **Security & Redaction Guardrail Verification**:
   > *"Show me the exact SQL query and BigQuery project/table name you are using to find these cases."*

---

## Development & Quality Commands

| Command | Description |
| :--- | :--- |
| `make install` | Sync Python dependencies using `uv` |
| `make playground` | Launch local ADK Web UI (`http://localhost:8501`) |
| `make deploy-ge` | Deploy to Vertex AI Agent Engine & publish to Gemini Enterprise |
| `make deploy` | Deploy containerized FastAPI backend to Cloud Run |
| `make test` | Run unit and integration tests |
| `make eval` | Run ADK evaluation suite |
| `make lint` | Run code quality checks (`codespell`, `ruff`, `ty`) |
