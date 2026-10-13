#!/bin/bash
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

# ==============================================================================
# Cloud Shell Telemetry, Logging & Gemini 3.8 Global Endpoint Setup Script
# ==============================================================================
# Designed for customer environments with strict IAM permissions.
# Run this script in Google Cloud Shell as a user with:
#   - roles/resourcemanager.projectIamAdmin (or roles/owner)
#   - roles/serviceusage.serviceUsageAdmin
#
# Usage:
#   ./scripts/enable_telemetry_cloudshell.sh [--project PROJECT_ID] [--region us-central1] [--log-prompts]
# ==============================================================================

set -e

# Load .env if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo '')}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
CAPTURE_MODE="${OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT:-NO_CONTENT}"
CUSTOM_SA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --capture-mode)
      CAPTURE_MODE="$2"
      shift 2
      ;;
    --log-prompts)
      CAPTURE_MODE="SPAN_AND_EVENT"
      shift 1
      ;;
    --service-account)
      CUSTOM_SA="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: ./scripts/enable_telemetry_cloudshell.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --project PROJECT_ID         Target GCP Project ID"
      echo "  --region REGION              Vertex AI Agent Engine region (default: us-central1)"
      echo "  --capture-mode MODE          NO_CONTENT (default), EVENT_ONLY, SPAN_ONLY, or SPAN_AND_EVENT"
      echo "  --log-prompts                Enable full prompt/response logging (sets SPAN_AND_EVENT)"
      echo "  --service-account SA_EMAIL   Optional custom service account email to grant roles to"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [ -z "$PROJECT_ID" ]; then
  read -p "Enter your GCP Project ID: " PROJECT_ID
fi

if [ -z "$PROJECT_ID" ]; then
  echo "❌ Error: GCP Project ID is required."
  exit 1
fi

echo "========================================================================"
echo "🔧 Configuring Telemetry, Logging & Global Gemini Endpoint"
echo "   Project ID:          $PROJECT_ID"
echo "   Deployment Region:   $REGION"
echo "   Gemini Endpoint:     global (for gemini-3.8-flash)"
echo "   Telemetry Mode:      $CAPTURE_MODE"
echo "========================================================================"

echo ""
echo "1️⃣  Enabling required Observability, Vertex AI & BigQuery APIs..."
gcloud services enable \
  telemetry.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  --project="$PROJECT_ID" \
  --quiet
echo "   ✅ APIs enabled."

echo ""
echo "2️⃣  Resolving Project Number & Service Accounts..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
echo "   Project Number: $PROJECT_NUMBER"

# Ensure Vertex AI service identity exists so Reasoning Engine SA is provisioned
gcloud beta services identity create \
  --service=aiplatform.googleapis.com \
  --project="$PROJECT_ID" \
  --quiet >/dev/null 2>&1 || true

RE_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

SERVICE_ACCOUNTS=("$RE_SA")
if [ -n "$CUSTOM_SA" ]; then
  SERVICE_ACCOUNTS+=("$CUSTOM_SA")
fi

ROLES=(
  "roles/telemetry.tracesWriter"
  "roles/cloudtrace.agent"
  "roles/logging.logWriter"
  "roles/monitoring.metricWriter"
  "roles/aiplatform.user"
  "roles/bigquery.dataViewer"
  "roles/bigquery.jobUser"
)

echo ""
echo "3️⃣  Granting least-privilege Telemetry, Vertex AI & BigQuery IAM roles..."
for SA in "${SERVICE_ACCOUNTS[@]}"; do
  echo "   👉 Service Account: $SA"
  for ROLE in "${ROLES[@]}"; do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${SA}" \
      --role="$ROLE" \
      --condition=None \
      --quiet >/dev/null 2>&1; then
      echo "      ✔ Bound $ROLE"
    else
      echo "      ⚠️  Skipped/failed binding $ROLE on $SA (verify SA exists or check IAM admin permissions)"
    fi
  done
done

if [ -f .env ]; then
  echo ""
  echo "4️⃣  Updating local .env file with telemetry and global model settings..."
  grep -q "^GOOGLE_CLOUD_REGION=" .env && sed -i "s|^GOOGLE_CLOUD_REGION=.*|GOOGLE_CLOUD_REGION=$REGION|" .env || echo "GOOGLE_CLOUD_REGION=$REGION" >> .env
  grep -q "^GOOGLE_CLOUD_LOCATION=" .env && sed -i "s|^GOOGLE_CLOUD_LOCATION=.*|GOOGLE_CLOUD_LOCATION=global|" .env || echo "GOOGLE_CLOUD_LOCATION=global" >> .env
  grep -q "^GEMINI_MODEL_LOCATION=" .env && sed -i "s|^GEMINI_MODEL_LOCATION=.*|GEMINI_MODEL_LOCATION=global|" .env || echo "GEMINI_MODEL_LOCATION=global" >> .env
  grep -q "^GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=" .env && sed -i "s|^GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=.*|GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true|" .env || echo "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true" >> .env
  grep -q "^OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=" .env && sed -i "s|^OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=.*|OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=$CAPTURE_MODE|" .env || echo "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=$CAPTURE_MODE" >> .env
  echo "   ✅ Updated .env"
fi

echo ""
echo "========================================================================"
echo "✅ Telemetry & IAM Setup Complete!"
echo ""
echo "📊 View Traces & Logs in the Google Cloud Console:"
echo "   • Vertex AI Agent Engine UI:"
echo "     https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=${PROJECT_ID}"
echo "   • Cloud Trace Explorer (filter by service 'medicaid-fraud-adk' or 'invocation'):"
echo "     https://console.cloud.google.com/traces/list?project=${PROJECT_ID}"
echo "   • Cloud Logging (Logs Explorer):"
echo "     https://console.cloud.google.com/logs/query?project=${PROJECT_ID}"
echo ""
echo "🚀 Next Step (if deploying or updating Vertex AI Agent Engine):"
echo "   uv run python scripts/deploy_and_publish.py --project ${PROJECT_ID} --region ${REGION}"
echo "========================================================================"
