# Security & Vulnerability Assessment Register

## Vulnerability Triage Status

### 1. [CVE-2026-81726](https://avd.aquasec.com/nvd/cve-2026-81726) - `nltk` Path Traversal
* **Package**: `nltk <= 3.10.3`
* **Severity**: HIGH
* **Status**: **NOT EXPLOITABLE / RISK ACCEPTED (OUT-OF-SCOPE)**
* **Triage Justification & Evidence**:
  1. **Transitive Offline Dependency**: `nltk` is not used in the application runtime (`app/`). It is pulled in transitively only via `google-adk[eval]` -> `rouge-score` for calculating offline lexical evaluation metrics (ROUGE-1 / ROUGE-L).
  2. **Attack Surface Infeasible**: CVE-2026-81726 pertains to path traversal during model checkpoint loading (`TransitionParser`, `AveragedPerceptron`, `PerceptronTagger`, `maxent`). The application does not invoke NLTK parser/tagger APIs, does not load NLTK binary model files, and does not accept user file paths for model deserialization.
  3. **Deployment Isolation**: Production deployments to Vertex AI Agent Engine bundle only runtime code and tools (`app/tools.py`, `app/agent.py`), excluding offline evaluation packages.

---

## Codebase Security Controls Implemented

1. **SQL Injection Defense (CWE-89 / Bandit B608)**:
   * All BigQuery operations use strict parameterization with `google.cloud.bigquery.ScalarQueryParameter`.
   * Dynamic string formatting in SQL `WHERE` clauses is eliminated.
   * Hard limits (`MAX_BYTES_BILLED = 100MB`, `MAX_ROW_LIMIT = 100`) prevent Denial of Wallet and container OOMs.

2. **Infrastructure Identifier Redaction (CWE-200)**:
   * LLM system prompts strictly enforce positive behavioral boundaries with no leakage of GCP project numbers or BigQuery dataset/table names.
   * Deterministic regex post-processing (`redact_sensitive_infrastructure`) and ADK `after_agent_callback` hooks actively sanitize all model and pipeline outputs.

3. **API Security & Rate Limiting (CWE-306, CWE-799)**:
   * `/feedback` endpoint protected with token authentication (`X-API-Key` / Bearer token) and IP sliding-window rate limiting.
   * Strict Pydantic v2 validation bounds (`score: ge=1, le=5`, `text: max_length=1000`).

4. **Command Execution Safety (Bandit B607)**:
   * All subprocess management scripts resolve absolute binary paths via `shutil.which`.
