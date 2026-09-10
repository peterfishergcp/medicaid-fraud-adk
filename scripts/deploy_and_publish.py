#!/usr/bin/env python3
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

"""End-to-end Deployment and Gemini Enterprise Publishing Workflow."""

import json
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ID = "ai-hub-459714"
REGION = "us-central1"
SERVICE_NAME = "Medicaid Application Auditor"
VERSION_TAG = "v2"

TARGET_APPS = [
    "projects/726122012742/locations/global/collections/default_collection/engines/frauddectorapp_1787760680542",
    "projects/726122012742/locations/global/collections/default_collection/engines/agentspace-adk_1749611082035",
]


def run_deployment() -> str:
    logger.info("=== STEP 1: Deploying to Vertex AI Agent Engine ===")
    deploy_cmd = [
        "uv",
        "run",
        "agents-cli",
        "deploy",
        "--deployment-target",
        "agent_runtime",
        "--project",
        PROJECT_ID,
        "--region",
        REGION,
        "--service-name",
        SERVICE_NAME,
        "--no-confirm-project",
    ]
    logger.info("Executing: %s", " ".join(deploy_cmd))
    res = subprocess.run(deploy_cmd, cwd=PROJECT_DIR, text=True)
    if res.returncode != 0:
        logger.error("Deployment to Agent Engine failed with exit code %s", res.returncode)
        sys.exit(res.returncode)

    metadata_file = PROJECT_DIR / "deployment_metadata.json"
    if not metadata_file.exists():
        logger.error("deployment_metadata.json not found after deployment!")
        sys.exit(1)

    with open(metadata_file) as f:
        metadata = json.load(f)

    runtime_id = metadata.get("remote_agent_runtime_id")
    if not runtime_id:
        logger.error("No remote_agent_runtime_id found in deployment_metadata.json")
        sys.exit(1)

    logger.info("Agent Engine deployment successful! Runtime ID: %s", runtime_id)
    return runtime_id


def run_publishing(runtime_id: str) -> None:
    logger.info("=== STEP 2: Publishing to Gemini Enterprise ===")
    publish_cmd = [
        "uv",
        "run",
        "python",
        "scripts/publish_ge.py",
        "--runtime-id",
        runtime_id,
        "--apps",
        *TARGET_APPS,
        "--version",
        VERSION_TAG,
    ]
    logger.info("Executing: %s", " ".join(publish_cmd))
    res = subprocess.run(publish_cmd, cwd=PROJECT_DIR, text=True)
    if res.returncode != 0:
        logger.error("Publishing to Gemini Enterprise failed with exit code %s", res.returncode)
        sys.exit(res.returncode)

    logger.info("=== All deployment and registration steps completed successfully! ===")


def main():
    runtime_id = run_deployment()
    run_publishing(runtime_id)


if __name__ == "__main__":
    main()
