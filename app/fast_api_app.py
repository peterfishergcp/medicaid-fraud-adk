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
import time
from collections import defaultdict
from typing import Annotated

import google.auth
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as google_cloud_logging

from app.app_utils.reasoning_engine_adapter import (
    attach_reasoning_engine_routes,
)
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

setup_telemetry()
_, project_id = google.auth.default()
logging_client = google_cloud_logging.Client()
logger = logging_client.logger(__name__)
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

# Artifact bucket for ADK (created by Terraform, passed via env var)
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# In-memory session configuration - no persistent storage
session_service_uri = None

artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=True,
)
app.title = "medicaid-fraud-adk"
app.description = "API for interacting with the Agent medicaid-fraud-adk"

# Attach Reasoning Engine endpoints for Vertex AI Agent Engine and Gemini Enterprise
attach_reasoning_engine_routes(app)

# Simple token/auth verification for secure internal endpoint access
BEARER_AUTH = HTTPBearer(auto_error=False)
EXPECTED_API_KEY = os.environ.get("FEEDBACK_API_KEY")

# Simple in-memory sliding-window rate limiter: max 10 requests per minute per client IP
RATE_LIMIT_WINDOW = 60  # seconds
MAX_REQUESTS_PER_WINDOW = 10
_request_history: dict[str, list[float]] = defaultdict(list)


def verify_feedback_auth(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER_AUTH)] = None,
) -> bool:
    """Verifies that the request provides valid API Key or Bearer token credentials when configured."""
    if EXPECTED_API_KEY:
        if x_api_key == EXPECTED_API_KEY:
            return True
        if bearer and bearer.credentials == EXPECTED_API_KEY:
            return True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication credentials for feedback submission.",
        )
    return True


def rate_limiter(request: Request) -> None:
    """Enforces rate limiting based on client host address."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = _request_history[client_ip]

    # Purge timestamps outside current window
    _request_history[client_ip] = [
        ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW
    ]

    if len(_request_history[client_ip]) >= MAX_REQUESTS_PER_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before submitting more feedback.",
        )
    _request_history[client_ip].append(now)


@app.post(
    "/feedback", dependencies=[Depends(rate_limiter), Depends(verify_feedback_auth)]
)
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback with authentication and rate limiting.

    Args:
        feedback: The validated feedback data to log.

    Returns:
        Success message.
    """
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
