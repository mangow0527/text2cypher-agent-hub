from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class GroundedLLMClient(Protocol):
    provider: str

    def generate_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        attempt: int,
    ) -> Mapping[str, Any]:
        """Return a provider-native structured object, not free-form text."""
