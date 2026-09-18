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
LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
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

read -p "Enter GCP Region/Location [default: $LOCATION]: " INPUT_LOCATION
LOCATION="${INPUT_LOCATION:-$LOCATION}"

read -p "Enter Gemini Enterprise App URI(s) (comma-separated, optional) [default: $GE_APPS]: " INPUT_GE_APPS
GE_APPS="${INPUT_GE_APPS:-$GE_APPS}"

# Create / Update .env file
echo ""
echo "📝 Saving configuration to .env..."
cat << EOF > .env
GOOGLE_CLOUD_PROJECT=$PROJECT_ID
GOOGLE_CLOUD_LOCATION=$LOCATION
BIGQUERY_DATASET=$DATASET
BIGQUERY_TABLE=$TABLE
GOOGLE_GENAI_USE_VERTEXAI=True
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
echo "   - Project ID:      $PROJECT_ID"
echo "   - Region:          $LOCATION"
echo "   - BigQuery Table:  $PROJECT_ID.$DATASET.$TABLE"
if [ -n "$GE_APPS" ]; then
  echo "   - GE App URI(s):   $GE_APPS"
fi
echo "========================================================"
echo ""
echo "▶️  Run 'make playground' to launch the local web UI!"
echo "▶️  Run 'uv run python scripts/deploy_and_publish.py' to deploy & publish!"
echo ""
