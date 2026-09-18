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


def normalize_app_uri(app_str: str, project_id: str) -> str:
    """Normalizes a bare Gemini Enterprise Engine ID into a full Discovery Engine resource URI."""
    app_str = app_str.strip()
    if app_str.startswith("projects/"):
        return app_str
    return f"projects/{project_id}/locations/global/collections/default_collection/engines/{app_str}"


def get_default_target_apps(project_id: str) -> list[str]:
    """Resolves target Gemini Enterprise App URIs from GEMINI_ENTERPRISE_APPS env var."""
    apps_env = os.environ.get("GEMINI_ENTERPRISE_APPS", "")
    return [
        normalize_app_uri(app, project_id)
        for app in apps_env.split(",")
        if app.strip()
    ]


def run_deployment(
    project_id: str,
    region: str,
    service_name: str,
    dataset: str,
    table: str,
) -> str:
    logger.info("=== STEP 1: Deploying to Vertex AI Agent Engine ===")
    logger.info(
        "Target Project: %s | Region: %s | BigQuery Table: %s.%s.%s",
        project_id,
        region,
        project_id,
        dataset,
        table,
    )
    env_vars_str = f"BIGQUERY_DATASET={dataset},BIGQUERY_TABLE={table}"
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
        "--update-env-vars",
        env_vars_str,
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
    default_project = get_default_project_id()
    parser = argparse.ArgumentParser(
        description="Deploy ADK Agent to Vertex AI Agent Engine and publish to Gemini Enterprise."
    )
    parser.add_argument(
        "--project",
        default=default_project,
        help="GCP Project ID (defaults to GOOGLE_CLOUD_PROJECT in .env or gcloud config)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="GCP Region for Vertex AI Agent Engine (default: us-central1)",
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("BIGQUERY_DATASET", "frauddector"),
        help="BigQuery Dataset name (defaults to BIGQUERY_DATASET in .env)",
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("BIGQUERY_TABLE", "syntheticdatafraud"),
        help="BigQuery Table name (defaults to BIGQUERY_TABLE in .env)",
    )
    parser.add_argument(
        "--service-name",
        default=DEFAULT_SERVICE_NAME,
        help=f"Agent Engine display name (default: '{DEFAULT_SERVICE_NAME}')",
    )
    parser.add_argument(
        "--apps",
        nargs="*",
        default=None,
        help="Target Gemini Enterprise App URIs or Engine IDs (defaults to GEMINI_ENTERPRISE_APPS in .env)",
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

    runtime_id = run_deployment(
        args.project, args.region, args.service_name, args.dataset, args.table
    )

    if args.skip_publish:
        logger.info("Skipping Gemini Enterprise publishing (--skip-publish set).")
        return

    if args.apps is not None:
        target_apps = [normalize_app_uri(a, args.project) for a in args.apps if a.strip()]
    else:
        target_apps = get_default_target_apps(args.project)

    if not target_apps and sys.stdin.isatty():
        logger.info("No GEMINI_ENTERPRISE_APPS configured in .env or --apps flag.")
        user_input = input(
            "Enter target Gemini Enterprise App URI or Engine ID (or press Enter to skip publishing): "
        ).strip()
        if user_input:
            target_apps = [
                normalize_app_uri(app, args.project)
                for app in user_input.split(",")
                if app.strip()
            ]

    if not target_apps:
        logger.warning(
            "No Gemini Enterprise App URIs provided. Skipping Step 2 (Publishing).\n"
            "To publish later, run:\n"
            "  uv run python scripts/publish_ge.py --runtime-id %s --apps <YOUR_GE_APP_URI_OR_ID>",
            runtime_id,
        )
        return

    run_publishing(runtime_id, target_apps, args.version, replace=args.replace)


if __name__ == "__main__":
    main()
