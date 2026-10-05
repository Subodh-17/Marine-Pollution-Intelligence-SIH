"""
Structured application logging.

Uses the Python standard library's `logging` module only (no new
dependency). Every log record emitted through `get_logger(__name__)` is
formatted as a single-line JSON object, so it can be grepped, shipped to a
log aggregator, or parsed by tooling without a custom parser.

WHAT IS LOGGED (per pipeline stage, via `app.investigation.orchestrator`):
  - investigation_id
  - pipeline stage name
  - stage/total processing duration (ms)
  - detector provenance (which detector actually produced the result)
  - fallback reason (if a fallback occurred)
  - number of AIS records processed
  - number of candidate vessels ranked

WHAT IS NEVER LOGGED:
  - secrets/credentials (none exist in this codebase - see app/config.py)
  - raw AIS record content (vessel positions/identities) or raw image
    pixel data - only aggregate counts and non-identifying metadata are
    logged, per the "do not log personal information" requirement.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Any extra structured fields passed via `logger.info(msg, extra={...})`
        # are merged in directly (investigation_id, stage, duration_ms, ...).
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_KEYS or key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_STANDARD_LOG_RECORD_KEYS = set(logging.LogRecord(
    "", 0, "", 0, "", (), None
).__dict__.keys()) | {"message", "asctime"}


def configure_logging(level: str = "INFO") -> None:
    """Idempotent - safe to call multiple times (e.g. once at app startup,
    once per test module) without duplicating handlers."""
    global _CONFIGURED
    root = logging.getLogger("marine_pollution_intel")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
        root.propagate = False
        _CONFIGURED = True
    else:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    """Returns a logger under the `marine_pollution_intel` namespace so all
    application logging shares one configured handler/formatter."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(f"marine_pollution_intel.{name}")


class StageTimer:
    """Tiny helper for measuring per-stage wall-clock duration without
    pulling in a profiling dependency. Usage:

        timer = StageTimer()
        ... do work ...
        elapsed_ms = timer.elapsed_ms()
    """

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000, 2)
