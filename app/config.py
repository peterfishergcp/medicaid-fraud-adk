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

# Resolve GCP Project ID from auth or environment
_, default_project = google.auth.default()
PROJECT_ID: str = (
    os.environ.get("GOOGLE_CLOUD_PROJECT") or default_project or "your-gcp-project-id"
)
LOCATION: str = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# BigQuery Dataset and Table targets
DATASET_NAME: str = os.environ.get("BIGQUERY_DATASET", "frauddetector")
TABLE_NAME: str = os.environ.get("BIGQUERY_TABLE", "syntheticdatafraud")
FULL_TABLE_REF: str = f"{PROJECT_ID}.{DATASET_NAME}.{TABLE_NAME}"

# Configure Google GenAI runtime defaults
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
