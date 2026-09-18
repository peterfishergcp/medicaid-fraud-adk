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

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "Medicaid Application Auditor"
DEFAULT_VERSION_TAG = "v2"


def get_default_project_id() -> str:
    """Resolves GCP Project ID from environment or active gcloud configuration."""
    env_proj = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    if env_proj:
        return env_proj.strip()

    gcloud_path = shutil.which("gcloud") or "gcloud"
    try:
        proj = subprocess.check_output(
            [gcloud_path, "config", "get-value", "project"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if proj and proj != "(unset)":
            return proj
    except Exception:
        pass
    return ""


def get_default_target_apps() -> list[str]:
    """Resolves target Gemini Enterprise App URIs from GEMINI_ENTERPRISE_APPS env var."""
    apps_env = os.environ.get("GEMINI_ENTERPRISE_APPS", "")
    return [app.strip() for app in apps_env.split(",") if app.strip()]


def run_deployment(project_id: str, region: str, service_name: str) -> str:
    logger.info("=== STEP 1: Deploying to Vertex AI Agent Engine ===")
    logger.info("Target Project: %s | Region: %s", project_id, region)
    deploy_cmd = [
        "uv",
        "run",
        "agents-cli",
        "deploy",
        "--deployment-target",
        "agent_runtime",
        "--project",
        project_id,
        "--region",
        region,
        "--service-name",
        service_name,
        "--no-confirm-project",
    ]
    logger.info("Executing: %s", " ".join(deploy_cmd))
    res = subprocess.run(deploy_cmd, cwd=PROJECT_DIR, text=True)
    if res.returncode != 0:
        logger.error(
            "Deployment to Agent Engine failed with exit code %s", res.returncode
        )
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


def run_publishing(
    runtime_id: str, target_apps: list[str], version_tag: str, replace: bool = False
) -> None:
    logger.info("=== STEP 2: Publishing to Gemini Enterprise ===")
    publish_cmd = [
        "uv",
        "run",
        "python",
        "scripts/publish_ge.py",
        "--runtime-id",
        runtime_id,
        "--apps",
        *target_apps,
        "--version",
        version_tag,
    ]
    if replace:
        publish_cmd.append("--replace")

    logger.info("Executing: %s", " ".join(publish_cmd))
    res = subprocess.run(publish_cmd, cwd=PROJECT_DIR, text=True)
    if res.returncode != 0:
        logger.error(
            "Publishing to Gemini Enterprise failed with exit code %s", res.returncode
        )
        sys.exit(res.returncode)

    logger.info("=== All deployment and registration steps completed successfully! ===")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy ADK Agent to Vertex AI Agent Engine and publish to Gemini Enterprise."
    )
    parser.add_argument(
        "--project",
        default=get_default_project_id(),
        help="GCP Project ID (defaults to GOOGLE_CLOUD_PROJECT in .env or gcloud config)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="GCP Region for Vertex AI Agent Engine (default: us-central1)",
    )
    parser.add_argument(
        "--service-name",
        default=DEFAULT_SERVICE_NAME,
        help=f"Agent Engine display name (default: '{DEFAULT_SERVICE_NAME}')",
    )
    parser.add_argument(
        "--apps",
        nargs="*",
        default=get_default_target_apps(),
        help="Target Gemini Enterprise App URIs (defaults to GEMINI_ENTERPRISE_APPS in .env)",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION_TAG,
        help=f"Version tag to prepend in Gemini Enterprise (default: '{DEFAULT_VERSION_TAG}')",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing agent registration matching this version tag",
    )
    parser.add_argument(
        "--skip-publish",
        action="store_true",
        help="Deploy to Vertex AI Agent Engine only, skipping Gemini Enterprise registration",
    )
    args = parser.parse_args()

    if not args.project:
        logger.error(
            "No GCP Project ID detected. Please run './install.sh', set GOOGLE_CLOUD_PROJECT in .env, "
            "or pass --project <your-project-id>."
        )
        sys.exit(1)

    runtime_id = run_deployment(args.project, args.region, args.service_name)

    if args.skip_publish:
        logger.info("Skipping Gemini Enterprise publishing (--skip-publish set).")
        return

    target_apps = args.apps
    if not target_apps and sys.stdin.isatty():
        logger.info("No GEMINI_ENTERPRISE_APPS configured in .env or --apps flag.")
        user_input = input(
            "Enter target Gemini Enterprise App URI (or press Enter to skip publishing): "
        ).strip()
        if user_input:
            target_apps = [app.strip() for app in user_input.split(",") if app.strip()]

    if not target_apps:
        logger.warning(
            "No Gemini Enterprise App URIs provided. Skipping Step 2 (Publishing).\n"
            "To publish later, run:\n"
            "  uv run python scripts/publish_ge.py --runtime-id %s --apps <YOUR_GE_APP_URI>",
            runtime_id,
        )
        return

    run_publishing(runtime_id, target_apps, args.version, replace=args.replace)


if __name__ == "__main__":
    main()
