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

"""
Automated Deployment & Registration Helper for Gemini Enterprise.
Performs zero-downtime / idempotent upsert by replacing existing agent registrations
with the latest Vertex AI Reasoning Engine runtime ID.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys

import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_APPS_ENV = os.environ.get("GEMINI_ENTERPRISE_APPS", "")
DEFAULT_APPS = [app.strip() for app in DEFAULT_APPS_ENV.split(",") if app.strip()]

DISPLAY_NAME = "Medicaid Application Auditor"
DESCRIPTION = "Analyzes Medicaid application extracts to identify highly suspicious, anomalous, or potentially fraudulent submissions using Gemini 3.8 Flash."


def get_gcp_access_token() -> str:
    """Retrieves Google Cloud IAM OAuth2 access token via gcloud."""
    gcloud_path = shutil.which("gcloud") or "gcloud"
    try:
        return subprocess.check_output(
            [gcloud_path, "auth", "print-access-token"], text=True
        ).strip()
    except Exception as e:
        logger.error(f"Failed to get GCP access token: {e}")
        sys.exit(1)


def cleanup_and_publish(
    reasoning_engine_uri: str,
    app_uri: str,
    token: str,
    dry_run: bool = False,
    force: bool = True,
) -> None:
    """Removes previous agent registrations strictly matching exact DISPLAY_NAME and registers the new runtime."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    app_id = app_uri.split("/")[-1]
    logger.info(f"Checking existing registrations in app: {app_id}...")

    list_url = f"https://discoveryengine.googleapis.com/v1alpha/{app_uri}/assistants/default_assistant/agents"
    try:
        resp = requests.get(list_url, headers=headers)
        if resp.status_code == 200:
            agents = resp.json().get("agents", [])
            for agent in agents:
                name = agent.get("name", "")
                agent_display = agent.get("displayName", "")
                agent_id = name.split("/")[-1]
                # Strictly match on exact unique display name (no broad substring matching)
                if agent_display == DISPLAY_NAME:
                    if dry_run:
                        logger.info(
                            f"[DRY RUN] Would delete exact match agent: {agent_id} ({agent_display})"
                        )
                        continue
                    if not force:
                        confirm = input(
                            f"Delete existing agent registration '{agent_display}' ({agent_id})? [y/N]: "
                        )
                        if confirm.lower() != "y":
                            logger.info(f"Skipping deletion of {agent_id}.")
                            continue

                    logger.info(
                        f"Removing outdated agent registration: {agent_id} ({agent_display})..."
                    )
                    del_resp = requests.delete(
                        f"https://discoveryengine.googleapis.com/v1alpha/{name}",
                        headers=headers,
                    )
                    if del_resp.status_code in (200, 204):
                        logger.info(f"Successfully removed {agent_id}.")
                    else:
                        logger.warning(f"Could not delete {agent_id}: {del_resp.text}")
    except Exception as e:
        logger.warning(f"Error checking existing agent registrations: {e}")

    if dry_run:
        logger.info(
            f"[DRY RUN] Would register new Reasoning Engine {reasoning_engine_uri} into {app_id}"
        )
        return

    # Register the newest agent via agents-cli
    logger.info(f"Registering new Reasoning Engine into {app_id}...")
    uv_path = shutil.which("uv") or "uv"
    cmd = [
        uv_path,
        "run",
        "agents-cli",
        "publish",
        "gemini-enterprise",
        "--agent-runtime-id",
        reasoning_engine_uri,
        "--gemini-enterprise-app-id",
        app_uri,
        "--display-name",
        DISPLAY_NAME,
        "--description",
        DESCRIPTION,
        "--registration-type",
        "adk",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        logger.info(f"Successfully registered agent in {app_id}!")
    else:
        logger.error(
            f"Failed to register agent in {app_id}:\n{res.stderr}\n{res.stdout}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish ADK Agent to Gemini Enterprise with safe exact-match cleanup."
    )
    parser.add_argument(
        "--runtime-id", required=True, help="Full Reasoning Engine Resource URI"
    )
    parser.add_argument(
        "--apps",
        nargs="*",
        default=DEFAULT_APPS,
        help="Target Gemini Enterprise App URIs (or set GEMINI_ENTERPRISE_APPS env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate cleanup and registration without modifying Gemini Enterprise.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for explicit confirmation before deleting any existing registration.",
    )
    args = parser.parse_args()

    if not args.apps:
        logger.error(
            "No Gemini Enterprise target application URIs provided. "
            "Please specify via --apps flag or GEMINI_ENTERPRISE_APPS environment variable."
        )
        sys.exit(1)

    token = get_gcp_access_token()
    for app_uri in args.apps:
        cleanup_and_publish(
            args.runtime_id,
            app_uri,
            token,
            dry_run=args.dry_run,
            force=not args.interactive,
        )


if __name__ == "__main__":
    main()
