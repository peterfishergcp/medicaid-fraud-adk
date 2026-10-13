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

import logging
import os


def setup_telemetry() -> str | None:
    """Configure OpenTelemetry and GenAI telemetry with Cloud Trace, Cloud Logging, and optional GCS upload."""
    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
    os.environ.setdefault("OTEL_SERVICE_NAME", "medicaid-fraud-adk")
    os.environ.setdefault(
        "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
    )
    commit_sha = os.environ.get("COMMIT_SHA", "dev")
    os.environ.setdefault(
        "OTEL_RESOURCE_ATTRIBUTES",
        f"service.namespace=medicaid-fraud-adk,service.version={commit_sha}",
    )

    raw_bucket = os.environ.get("LOGS_BUCKET_NAME")
    bucket = raw_bucket.removeprefix("gs://").rstrip("/") if raw_bucket else None
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT"
    )
    if capture_content.lower() == "true":
        capture_content = "SPAN_AND_EVENT"
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = (
            capture_content
        )
    elif capture_content.lower() != "false":
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT"
        )

    if bucket and capture_content.lower() != "false":
        logging.info(
            "Prompt-response GCS logging enabled - mode: %s",
            os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"],
        )
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
    else:
        logging.info(
            "Cloud Trace & OpenTelemetry enabled (mode=%s). Optional GCS upload disabled (set LOGS_BUCKET_NAME to enable).",
            os.environ.get(
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT"
            ),
        )

    return bucket
