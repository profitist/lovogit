import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lovogit.app.project_update.artifacts import write_json
from lovogit.cfg.project_update import TraceExportSettings


async def export_trace(
    folder: Path, settings: TraceExportSettings, *, transport=None
) -> dict:
    url = urlsplit(settings.LANGFUSE_HOST)
    if (
        not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or not (
            url.scheme == "https"
            or url.scheme == "http"
            and url.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        or not settings.LANGFUSE_PUBLIC_KEY.get_secret_value().strip()
        or not settings.LANGFUSE_SECRET_KEY.get_secret_value().strip()
    ):
        raise ValueError("Trace export requires a Langfuse origin and nonempty keys")
    payload = (folder / "langfuse-otel.json").read_bytes()
    parsed = json.loads(payload)
    payload_hash = hashlib.sha256(payload).hexdigest()
    receipt = folder / "langfuse-export.json"
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        if previous.get("status") in {"accepted", "partial", "unknown"}:
            raise ValueError(
                "Trace export refused: payload may already have been accepted"
            )
    spans = parsed["resourceSpans"][0]["scopeSpans"][0]["spans"]
    result = {
        "status": "unknown",
        "payload_sha256": payload_hash,
        "trace_id": spans[0]["traceId"] if spans else None,
    }
    write_json(receipt, result)
    try:
        async with httpx.AsyncClient(
            timeout=30, trust_env=False, transport=transport
        ) as client:
            response = await client.post(
                settings.LANGFUSE_HOST.rstrip("/") + "/api/public/otel/v1/traces",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-langfuse-ingestion-version": "4",
                },
                auth=(
                    settings.LANGFUSE_PUBLIC_KEY.get_secret_value(),
                    settings.LANGFUSE_SECRET_KEY.get_secret_value(),
                ),
            )
        result["http_status"] = response.status_code
        if response.status_code == 200:
            partial = (
                response.json().get("partialSuccess", {}) if response.content else {}
            )
            result["status"] = (
                "partial"
                if int(partial.get("rejectedSpans", 0)) or partial.get("errorMessage")
                else "accepted"
            )
        elif 400 <= response.status_code < 500:
            result["status"] = "failed"
        else:
            result["reason"] = "acceptance_unknown"
    except httpx.HTTPError, ValueError, TypeError, AttributeError:
        result["reason"] = "transport_or_response_error"
    finally:
        write_json(receipt, result)
    return result
