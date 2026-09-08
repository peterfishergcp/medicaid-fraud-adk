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
from dotenv import load_dotenv

load_dotenv()

# Google Cloud project & region configuration
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "ai-hub-459714")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "True").lower() in ("true", "1", "t")

# BigQuery dataset and table configuration
DATASET_ID = os.getenv("BIGQUERY_DATASET", "frauddetector")
TABLE_ID = os.getenv("BIGQUERY_TABLE", "syntheticdatafraud")

# Fully qualified BigQuery table reference (e.g. `ai-hub-459714.frauddetector.syntheticdatafraud`)
FULL_TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
