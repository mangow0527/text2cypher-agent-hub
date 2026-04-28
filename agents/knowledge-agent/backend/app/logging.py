from __future__ import annotations

import logging
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.config import settings


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging() -> logging.Logger:
    log_dir: Path = settings.artifacts_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "knowledge-agent.log"

    logger = logging.getLogger("knowledge-agent")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


class ModuleLogStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.artifacts_dir / "logs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, module: str) -> Path:
        return self.root / f"{module}.log"

    def append(
        self,
        module: str,
        level: str,
        operation: str,
        trace_id: str | None = None,
        status: str | None = None,
        request_body: Any | None = None,
        response_body: Any | None = None,
        **extra: Any,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "module": module,
            "level": level,
            "operation": operation,
            "trace_id": trace_id,
            "status": status,
        }
        if request_body is not None:
            entry["request_body"] = request_body
        if response_body is not None:
            entry["response_body"] = response_body
        if extra:
            entry.update({key: value for key, value in extra.items() if value is not None})
        with self.path_for(module).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
