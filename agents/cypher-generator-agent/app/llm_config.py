from __future__ import annotations


def enforce_required_llm_config(
    *,
    service_name: str,
    llm_enabled: bool,
    llm_base_url: str | None,
    llm_api_key: str | None,
    llm_model: str | None,
) -> None:
    if not llm_enabled:
        raise ValueError(f"{service_name} requires LLM mode and does not support disabling it.")

    missing_fields = [
        field_name
        for field_name, value in (
            ("llm_base_url", llm_base_url),
            ("llm_api_key", llm_api_key),
            ("llm_model", llm_model),
        )
        if not value
    ]
    if missing_fields:
        raise ValueError(
            f"{service_name} requires complete LLM configuration; missing: {', '.join(missing_fields)}."
        )
