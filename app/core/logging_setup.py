"""
Structured logging.

Two outputs per run:
  * console   - readable text: "time | LEVEL | logger | message key=value"
  * JSON file - logs/<run_name>_<YYYYMMDD>.jsonl, one JSON object per line
                with timestamp, level, logger, message and any extra fields

Usage:
    from app.core.logging_setup import get_logger
    log = get_logger(__name__, run_name="discovery")
    log.info("filings fetched", extra={"fields": {"rows": 1250}})
"""

import json
import logging
from datetime import datetime, timezone

from app.config import settings


_CONFIGURED_RUNS = set()


class _TextFormatter(logging.Formatter):
    def format(self, record):
        base = (f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} | "
                f"{record.levelname:<7} | {record.name} | "
                f"{record.getMessage()}")
        extra = getattr(record, "fields", None)
        if extra:
            base += " " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update({k: _jsonable(v) for k, v in extra.items()})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def get_logger(name, run_name="platform"):
    """
    Return a logger writing to the console and to
    logs/<run_name>_<YYYYMMDD>.jsonl. Safe to call repeatedly.
    """

    root = logging.getLogger("brsr")
    if run_name not in _CONFIGURED_RUNS:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        root.setLevel(settings.log_level.upper())
        root.propagate = False

        if not any(isinstance(h, logging.StreamHandler)
                   and not isinstance(h, logging.FileHandler)
                   for h in root.handlers):
            console = logging.StreamHandler()
            console.setFormatter(_TextFormatter())
            root.addHandler(console)

        stamp = datetime.now().strftime("%Y%m%d")
        file_handler = logging.FileHandler(
            settings.log_dir / f"{run_name}_{stamp}.jsonl",
            encoding="utf-8")
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)
        _CONFIGURED_RUNS.add(run_name)

    short = name.split("app.")[-1] if name.startswith("app.") else name
    return root.getChild(short)
