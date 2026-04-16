from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import settings as _settings
from app.domain.models import SchemaSourceConfig, TuGraphConfig, TuGraphSourceConfig
from app.errors import AppError


class SourceResolver:
    def resolve_schema(self, source: SchemaSourceConfig, fallback_input: Any = None) -> Any:
        if source.type == "inline":
            schema = source.inline_json if source.inline_json is not None else fallback_input
            if schema is None:
                raise AppError("SCHEMA_PARSE_ERROR", "Inline schema content is empty.")
            return schema

        if source.type == "file":
            if not source.file_path:
                raise AppError("SCHEMA_PARSE_ERROR", "Schema file path is required.")
            path = Path(source.file_path).expanduser().resolve()
            if not path.exists():
                raise AppError("SCHEMA_PARSE_ERROR", f"Schema file does not exist: {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        if source.type == "url":
            if not source.url:
                raise AppError("SCHEMA_PARSE_ERROR", "Schema URL is required.")
            response = httpx.request(
                source.method,
                source.url,
                headers=source.headers,
                json=source.body,
                timeout=60,
            )
            response.raise_for_status()
            return response.json()

        raise AppError("SCHEMA_PARSE_ERROR", f"Unsupported schema source type: {source.type}")

    def resolve_tugraph(self, source: TuGraphSourceConfig, config: TuGraphConfig) -> TuGraphConfig:
        if source.type == "inline":
            if any([config.base_url, config.username, config.password, config.graph]):
                return config
            return TuGraphConfig(
                base_url=os.getenv("TUGRAPH_BASE_URL") or config.base_url,
                username=os.getenv("TUGRAPH_USER") or config.username,
                password=os.getenv("TUGRAPH_PASSWORD") or config.password,
                graph=os.getenv("TUGRAPH_GRAPH") or config.graph,
                cypher_endpoint=config.cypher_endpoint,
                timeout_seconds=config.timeout_seconds,
            )
        return TuGraphConfig(
            base_url=os.getenv("TUGRAPH_BASE_URL") or config.base_url,
            username=os.getenv("TUGRAPH_USER") or config.username,
            password=os.getenv("TUGRAPH_PASSWORD") or config.password,
            graph=os.getenv("TUGRAPH_GRAPH") or config.graph,
            cypher_endpoint=config.cypher_endpoint,
            timeout_seconds=config.timeout_seconds,
        )
