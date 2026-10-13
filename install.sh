#!/bin/bash
set -e

echo "========================================================"
echo "🚀 Medicaid Application Auditor ADK Agent - Setup"
echo "========================================================"

# Load .env file if present
if [ -f .env ]; then
  echo "📄 Loading environment variables from .env..."
  export $(grep -v '^#' .env | xargs)
fi

# Detect defaults
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo '')}"
if [ -n "$GOOGLE_CLOUD_REGION" ]; then
  REGION="$GOOGLE_CLOUD_REGION"
elif [ -n "$GOOGLE_CLOUD_LOCATION" ] && [ "$GOOGLE_CLOUD_LOCATION" != "global" ]; then
  REGION="$GOOGLE_CLOUD_LOCATION"
else
  REGION="us-central1"
fi
DATASET="${BIGQUERY_DATASET:-frauddector}"
TABLE="${BIGQUERY_TABLE:-syntheticdatafraud}"
GE_APPS="${GEMINI_ENTERPRISE_APPS:-}"

# Interactive prompts if variables are not set
if [ -z "$PROJECT_ID" ]; then
  read -p "Enter your GCP Project ID: " PROJECT_ID
else
  read -p "Enter your GCP Project ID [default: $PROJECT_ID]: " INPUT_PROJECT_ID
  PROJECT_ID="${INPUT_PROJECT_ID:-$PROJECT_ID}"
fi

read -p "Enter BigQuery Dataset Name [default: $DATASET]: " INPUT_DATASET
DATASET="${INPUT_DATASET:-$DATASET}"

read -p "Enter BigQuery Table Name [default: $TABLE]: " INPUT_TABLE
TABLE="${INPUT_TABLE:-$TABLE}"

read -p "Enter GCP Deployment Region (e.g. us-central1) [default: $REGION]: " INPUT_REGION
REGION="${INPUT_REGION:-$REGION}"

read -p "Enter Gemini Enterprise App URI(s) (comma-separated, optional) [default: $GE_APPS]: " INPUT_GE_APPS
GE_APPS="${INPUT_GE_APPS:-$GE_APPS}"

# Create / Update .env file
echo ""
echo "📝 Saving configuration to .env..."
cat << EOF > .env
GOOGLE_CLOUD_PROJECT=$PROJECT_ID
GOOGLE_CLOUD_REGION=$REGION
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL_LOCATION=global
BIGQUERY_DATASET=$DATASET
BIGQUERY_TABLE=$TABLE
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT
GEMINI_ENTERPRISE_APPS=$GE_APPS
EOF

# 1. Install Python package manager 'uv' if missing
echo ""
if ! command -v uv &> /dev/null; then
  echo "📦 Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  source $HOME/.local/bin/env
fi

echo "📦 Installing Python dependencies with uv..."
uv sync

echo ""
echo "========================================================"
echo "✅ Setup Complete!"
echo "📌 Configuration saved in .env:"
echo "   - Project ID:        $PROJECT_ID"
echo "   - Deploy Region:     $REGION"
echo "   - Gemini Endpoint:   global (required for gemini-3.8-flash)"
echo "   - BigQuery Table:    $PROJECT_ID.$DATASET.$TABLE"
echo "   - Telemetry/Trace:   Enabled (Cloud Trace + Logging)"
if [ -n "$GE_APPS" ]; then
  echo "   - GE App URI(s):     $GE_APPS"
fi
echo "========================================================"
echo ""
echo "▶️  Run './scripts/enable_telemetry_cloudshell.sh' in Cloud Shell to configure IAM & Telemetry APIs!"
echo "▶️  Run 'make playground' to launch the local web UI!"
echo "▶️  Run 'uv run python scripts/deploy_and_publish.py' to deploy & publish!"
echo ""
