"""Optional, scrubbed per-stage App Insights events.

Only stage metadata is emitted: no person IDs, encounter IDs, record IDs, note text,
output text, withheld counts, or other-person data. Each request uses a fresh opaque
trace ID to correlate its stages. Telemetry is best-effort and never gates or fails a
briefing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

import httpx

log = logging.getLogger(__name__)


class TraceBuffer:
    def __init__(self):
        self.trace_id = uuid4().hex
        self.events: list[dict] = []

    def record(self, stage: str, elapsed_ms: int, *, model: str | None = None,
               input_tokens: int | None = None, output_tokens: int | None = None,
               status: str | None = None, path: str | None = None,
               error: str | None = None, http_status: int | None = None,
               request_id: str | None = None) -> None:
        event = {"stage": stage, "elapsed_ms": max(0, int(elapsed_ms))}
        if model:
            event["model"] = model
        if path:
            event["path"] = path
        if error:
            event["error"] = error
        if http_status is not None:
            event["http_status"] = int(http_status)
        if request_id:
            event["request_id"] = request_id
        if input_tokens is not None:
            event["input_tokens"] = max(0, int(input_tokens))
        if output_tokens is not None:
            event["output_tokens"] = max(0, int(output_tokens))
        if status:
            event["status"] = status
        self.events.append(event)

    async def flush(self) -> None:
        raw = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
        parts = dict(piece.split("=", 1) for piece in raw.split(";") if "=" in piece)
        instrumentation_key = parts.get("InstrumentationKey")
        ingestion_endpoint = parts.get("IngestionEndpoint", "https://dc.services.visualstudio.com").rstrip("/")
        if not instrumentation_key or not self.events:
            return
        sent_at = datetime.now(timezone.utc).isoformat()
        payload = [{
            "name": "Microsoft.ApplicationInsights.Event",
            "time": sent_at,
            "iKey": instrumentation_key,
            "data": {"baseType": "EventData", "baseData": {
                "ver": 2,
                "name": "luvn.pipeline.stage",
                "properties": {
                    "trace_id": self.trace_id,
                    **event,
                },
            }},
        } for event in self.events]
        try:
            async with asyncio.timeout(0.5):
                async with httpx.AsyncClient(timeout=httpx.Timeout(0.4, connect=0.2),
                                             trust_env=True, follow_redirects=False) as client:
                    response = await client.post(
                        f"{ingestion_endpoint}/v2/track",
                        content=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
        except Exception as exc:  # telemetry is optional and must never break a request
            log.warning("App Insights stage telemetry failed: %s", type(exc).__name__)


_active: ContextVar[TraceBuffer | None] = ContextVar("luvn_trace", default=None)


@contextmanager
def request_trace():
    buffer = TraceBuffer()
    token = _active.set(buffer)
    try:
        yield buffer
    finally:
        _active.reset(token)


def record(stage: str, started: float, *, model: str | None = None,
           input_tokens: int | None = None, output_tokens: int | None = None,
           status: str | None = None, path: str | None = None, error: str | None = None,
           http_status: int | None = None, request_id: str | None = None) -> None:
    buffer = _active.get()
    if buffer is not None:
        buffer.record(stage, round((time.monotonic() - started) * 1000), model=model,
                      input_tokens=input_tokens, output_tokens=output_tokens, status=status,
                      path=path, error=error, http_status=http_status, request_id=request_id)
