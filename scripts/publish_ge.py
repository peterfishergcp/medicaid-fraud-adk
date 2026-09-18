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

"""Automated Deployment & Registration Helper for Gemini Enterprise.

Registers the latest Vertex AI Reasoning Engine runtime ID into Gemini Enterprise.
Supports prepending version tags to the beginning of the display name
(e.g., 'v2 - Medicaid Application Auditor') and preserving existing agent
registrations so multiple versions can coexist side-by-side.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEFAULT_APPS_ENV = os.environ.get("GEMINI_ENTERPRISE_APPS", "")
DEFAULT_APPS = [app.strip() for app in DEFAULT_APPS_ENV.split(",") if app.strip()]

BASE_DISPLAY_NAME = "Medicaid Application Auditor"
DESCRIPTION = (
    "Analyzes Medicaid application extracts to identify highly suspicious, "
    "anomalous, or potentially fraudulent submissions using Gemini 3.8 Flash."
)


def format_versioned_display_name(base_name: str, version: str) -> str:
    """Prepends version tag to the beginning of the display name for UI visibility."""
    v = (version or "").strip()
    if not v:
        return base_name
    if v.startswith("[") and v.endswith("]"):
        return f"{v} {base_name}"
    return f"{v} - {base_name}"


def get_gcp_access_token() -> str:
    """Retrieves Google Cloud IAM OAuth2 access token via gcloud."""
    gcloud_path = shutil.which("gcloud") or "gcloud"
    try:
        return subprocess.check_output(
            [gcloud_path, "auth", "print-access-token"], text=True
        ).strip()
    except Exception as e:
        logger.error("Failed to get GCP access token: %s", e)
        sys.exit(1)


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


def delete_agent_registration(
    name: str,
    agent_id: str,
    display_name: str,
    headers: dict[str, str],
    dry_run: bool = False,
    force: bool = True,
) -> None:
    """Safely removes an existing agent registration by Discovery Engine resource name."""
    if dry_run:
        logger.info(
            "[DRY RUN] Would delete matching version agent: %s (%s)",
            agent_id,
            display_name,
        )
        return

    if not force:
        confirm = input(
            f"Delete existing agent registration '{display_name}' ({agent_id})? [y/N]: "
        )
        if confirm.lower() != "y":
            logger.info("Skipping deletion of %s.", agent_id)
            return

    logger.info("Removing existing registration for '%s': %s...", display_name, agent_id)
    del_resp = requests.delete(
        f"https://discoveryengine.googleapis.com/v1alpha/{name}",
        headers=headers,
    )
    if del_resp.status_code in (200, 204):
        logger.info("Successfully removed %s.", agent_id)
    else:
        logger.warning("Could not delete %s: %s", agent_id, del_resp.text)


def cleanup_matching_registrations(
    app_uri: str,
    display_name: str,
    headers: dict[str, str],
    dry_run: bool = False,
    force: bool = True,
) -> None:
    """Finds and removes existing agent registrations with matching display name."""
    app_id = app_uri.split("/")[-1]
    logger.info("Checking for existing registrations matching '%s' in app: %s...", display_name, app_id)
    list_url = f"https://discoveryengine.googleapis.com/v1alpha/{app_uri}/assistants/default_assistant/agents"
    try:
        resp = requests.get(list_url, headers=headers)
        if resp.status_code != 200:
            return
        agents = resp.json().get("agents", [])
        for agent in agents:
            name = agent.get("name", "")
            agent_display = agent.get("displayName", "")
            agent_id = name.split("/")[-1]
            if agent_display == display_name:
                delete_agent_registration(
                    name, agent_id, agent_display, headers, dry_run=dry_run, force=force
                )
    except Exception as e:
        logger.warning("Error checking existing agent registrations: %s", e)


def register_agent_runtime(
    reasoning_engine_uri: str,
    app_uri: str,
    display_name: str,
    headers: dict[str, str],
) -> None:
    """Registers a versioned agent into Gemini Enterprise via Discovery Engine REST API.

    Directly posts a new agent definition matching on display_name. This prevents
    agents-cli from performing an in-place rename of previous versions sharing the same
    underlying Reasoning Engine.
    """
    app_id = app_uri.split("/")[-1]
    list_url = f"https://discoveryengine.googleapis.com/v1alpha/{app_uri}/assistants/default_assistant/agents"

    existing_agent_res_name = None
    try:
        list_resp = requests.get(list_url, headers=headers)
        if list_resp.status_code == 200:
            for a in list_resp.json().get("agents", []):
                if a.get("displayName") == display_name:
                    existing_agent_res_name = a.get("name")
                    break
    except Exception as e:
        logger.warning("Error checking existing agents in %s: %s", app_id, e)

    payload = {
        "displayName": display_name,
        "description": DESCRIPTION,
        "icon": {
            "uri": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/smart_toy/default/24px.svg"
        },
        "adkAgentDefinition": {
            "toolSettings": {"toolDescription": DESCRIPTION},
            "provisionedReasoningEngine": {"reasoningEngine": reasoning_engine_uri},
        },
    }

    if existing_agent_res_name:
        logger.info("Updating existing registration for '%s' in %s...", display_name, app_id)
        resp = requests.patch(
            f"https://discoveryengine.googleapis.com/v1alpha/{existing_agent_res_name}",
            headers=headers,
            json=payload,
        )
    else:
        logger.info("Creating new versioned registration for '%s' in %s...", display_name, app_id)
        resp = requests.post(list_url, headers=headers, json=payload)

    if resp.status_code == 200:
        agent_id = resp.json().get("name", "").split("/")[-1]
        logger.info("Successfully registered '%s' in %s! (Agent ID: %s)", display_name, app_id, agent_id)
    else:
        logger.error("Failed to register agent in %s: %s %s", app_id, resp.status_code, resp.text)


def publish_agent(
    reasoning_engine_uri: str,
    app_uri: str,
    token: str,
    display_name: str,
    dry_run: bool = False,
    replace: bool = False,
    force: bool = True,
) -> None:
    """Publishes agent runtime to Gemini Enterprise, preserving existing versions by default."""
    project_id = get_default_project_id()
    if not app_uri.startswith("projects/") and project_id:
        app_uri = f"projects/{project_id}/locations/global/collections/default_collection/engines/{app_uri.strip()}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if project_id:
        headers["X-Goog-User-Project"] = project_id

    app_id = app_uri.split("/")[-1]

    if replace:
        cleanup_matching_registrations(
            app_uri, display_name, headers, dry_run=dry_run, force=force
        )
    else:
        logger.info(
            "Preserving existing agent registrations in %s (registering version: '%s')...",
            app_id,
            display_name,
        )

    if dry_run:
        logger.info(
            "[DRY RUN] Would register Reasoning Engine %s with display name '%s' into %s",
            reasoning_engine_uri,
            display_name,
            app_id,
        )
        return

    register_agent_runtime(reasoning_engine_uri, app_uri, display_name, headers)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish ADK Agent to Gemini Enterprise with versioning support."
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
        "--version",
        default="v2",
        help="Version tag to prepend to display name (e.g. 'v2' produces 'v2 - Medicaid Application Auditor'). Default is 'v2'.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        default=False,
        help="If set, removes existing registration matching this exact versioned display name before registering. Default is False (preserves existing registrations).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate registration without modifying Gemini Enterprise.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for confirmation before deleting any existing registration (when --replace is set).",
    )
    args = parser.parse_args()

    if not args.apps:
        logger.error(
            "No Gemini Enterprise target application URIs provided. "
            "Please specify via --apps flag or GEMINI_ENTERPRISE_APPS environment variable."
        )
        sys.exit(1)

    display_name = format_versioned_display_name(BASE_DISPLAY_NAME, args.version)
    token = get_gcp_access_token()
    for app_uri in args.apps:
        publish_agent(
            args.runtime_id,
            app_uri,
            token,
            display_name=display_name,
            dry_run=args.dry_run,
            replace=args.replace,
            force=not args.interactive,
        )


if __name__ == "__main__":
    main()
