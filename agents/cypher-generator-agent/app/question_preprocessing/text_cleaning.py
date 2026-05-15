from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml


DEFAULT_TEXT_CLEANING_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "question_preprocessing"
    / "text_cleaning.yaml"
)

_CJK_RE = r"\u4e00-\u9fff"


@dataclass(frozen=True)
class TextNormalization:
    """A single observable edit made during text cleaning."""

    rule: str
    from_text: str
    to_text: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "from": self.from_text,
            "to": self.to_text,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CleanTextResult:
    """Text cleaning output passed to later preprocessing stages."""

    cleaned_question: str
    changed: bool
    normalizations: tuple[TextNormalization, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "cleaned_question": self.cleaned_question,
            "changed": self.changed,
            "normalizations": [normalization.to_dict() for normalization in self.normalizations],
        }


def clean_text(
    original_question: str,
    *,
    config: dict[str, Any] | None = None,
) -> CleanTextResult:
    """Apply character-level cleaning without rewriting the question."""

    if not isinstance(original_question, str):
        raise TypeError("original_question must be a string")

    cleaning_config = config if config is not None else load_text_cleaning_config()
    normalizations: list[TextNormalization] = []
    text = original_question

    # Order matters:
    # - collapse whitespace before pattern-based rules so all spacing is stable;
    # - repair whitelist split words before deleting generic CJK inner spaces,
    #   otherwise "返 回" would disappear into a less explainable space edit.
    text = _trim(text, normalizations)
    text = _collapse_whitespace(text, cleaning_config, normalizations)
    text = _compress_duplicate_punctuation(text, cleaning_config, normalizations)
    text = _insert_light_punctuation_before_boundary_phrase(text, cleaning_config, normalizations)
    text = _repair_split_words_by_whitelist(text, cleaning_config, normalizations)
    text = _remove_safe_chinese_inner_spaces(text, cleaning_config, normalizations)

    return CleanTextResult(
        cleaned_question=text,
        changed=text != original_question,
        normalizations=tuple(normalizations),
    )


def load_text_cleaning_config(path: Path | None = None) -> dict[str, Any]:
    """Load a YAML cleaning config; tests may pass a custom path or mapping."""

    if path is None:
        return _load_default_text_cleaning_config()
    return _load_yaml_mapping(path)


@lru_cache(maxsize=1)
def _load_default_text_cleaning_config() -> dict[str, Any]:
    return _load_yaml_mapping(DEFAULT_TEXT_CLEANING_CONFIG_PATH)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"text cleaning config must be a mapping: {path}")
    return value


def _trim(text: str, normalizations: list[TextNormalization]) -> str:
    cleaned = text.strip()
    if cleaned != text:
        normalizations.append(
            TextNormalization(
                rule="trim",
                from_text=text,
                to_text=cleaned,
                reason="trim",
            )
        )
    return cleaned


def _collapse_whitespace(
    text: str,
    config: dict[str, Any],
    normalizations: list[TextNormalization],
) -> str:
    whitespace_policy = config.get("whitespace_policy", {})
    if not _enabled(whitespace_policy.get("collapse_whitespace")):
        return text

    # Keep one ASCII space as the neutral separator after normalizing tabs,
    # newlines, full runs of spaces, and other Python-recognized whitespace.
    pattern = re.compile(r"\s+")
    matches = [match.group(0) for match in pattern.finditer(text) if match.group(0) != " "]
    cleaned = pattern.sub(" ", text)
    if cleaned != text:
        for value in matches:
            normalizations.append(
                TextNormalization(
                    rule="collapse_whitespace",
                    from_text=value,
                    to_text=" ",
                    reason="extra_space",
                )
            )
    return cleaned


def _compress_duplicate_punctuation(
    text: str,
    config: dict[str, Any],
    normalizations: list[TextNormalization],
) -> str:
    punctuation_policy = config.get("punctuation_policy", {})
    collapse_rules = _mapping_items(punctuation_policy.get("collapse_rules"))

    # The YAML owns which punctuation groups collapse into which target mark.
    for rule in _collapse_rules_in_match_order(collapse_rules):
        chars = _string_list(rule.get("chars"))
        replacement = rule.get("to")
        if not chars or not isinstance(replacement, str):
            continue

        pattern = re.compile(f"[{''.join(re.escape(char) for char in chars)}]{{2,}}")

        def replace(match: re.Match[str]) -> str:
            source = match.group(0)
            normalizations.append(
                TextNormalization(
                    rule="compress_duplicate_punctuation",
                    from_text=source,
                    to_text=replacement,
                    reason="duplicate_punctuation",
                )
            )
            return replacement

        text = pattern.sub(replace, text)

    return text


def _insert_light_punctuation_before_boundary_phrase(
    text: str,
    config: dict[str, Any],
    normalizations: list[TextNormalization],
) -> str:
    punctuation_policy = config.get("punctuation_policy", {})
    insertion_policy = punctuation_policy.get("light_punctuation_insertion", {})
    before_phrases = insertion_policy.get("before_phrases", {})
    if not _enabled(before_phrases):
        return text

    punctuation = before_phrases.get("punctuation", "，")
    if not isinstance(punctuation, str) or not punctuation:
        return text

    phrases = _string_list(before_phrases.get("items"))
    if not phrases:
        return text

    # 这里只把配置项当作“补标点触发短语”，不解释它的语义。
    # 真正的自我修正判断由后续步骤根据短语识别结果完成。
    phrase_pattern = "|".join(re.escape(phrase) for phrase in sorted(phrases, key=len, reverse=True))
    pattern = re.compile(rf"(?P<left>[{_CJK_RE}A-Za-z0-9_]+)\s+(?P<phrase>{phrase_pattern})")

    def replace(match: re.Match[str]) -> str:
        target = f"{match.group('left')}{punctuation}{match.group('phrase')}"
        source_for_log, target_for_log = _boundary_phrase_log_span(
            match.group("left"),
            match.group("phrase"),
            punctuation,
        )
        normalizations.append(
            TextNormalization(
                rule="insert_light_punctuation_before_boundary_phrase",
                from_text=source_for_log,
                to_text=target_for_log,
                reason="missing_light_punctuation",
            )
        )
        return target

    return pattern.sub(replace, text)


def _repair_split_words_by_whitelist(
    text: str,
    config: dict[str, Any],
    normalizations: list[TextNormalization],
) -> str:
    # This is intentionally whitelist-only: it fixes common input splits
    # without guessing business terminology.
    for item in _mapping_items(config.get("split_word_repairs", {}).get("items")):
        source = item.get("from")
        target = item.get("to")
        if not isinstance(source, str) or not isinstance(target, str) or not source:
            continue
        if source not in text:
            continue
        count = text.count(source)
        text = text.replace(source, target)
        for _ in range(count):
            normalizations.append(
                TextNormalization(
                    rule="repair_split_words_by_whitelist",
                    from_text=source,
                    to_text=target,
                    reason="split_common_word",
                )
            )
    return text


def _remove_safe_chinese_inner_spaces(
    text: str,
    config: dict[str, Any],
    normalizations: list[TextNormalization],
) -> str:
    whitespace_policy = config.get("whitespace_policy", {})
    if not _enabled(whitespace_policy.get("remove_safe_chinese_inner_spaces")):
        return text

    # Only remove a single ASCII space between two CJK characters. This keeps
    # mixed-language text such as "Gold 服务" intact for later stages.
    pattern = re.compile(rf"(?P<left>[{_CJK_RE}]) (?P<right>[{_CJK_RE}])")
    while True:
        match = pattern.search(text)
        if match is None:
            return text

        source = match.group(0)
        target = f"{match.group('left')}{match.group('right')}"
        normalizations.append(
            TextNormalization(
                rule="remove_safe_chinese_inner_spaces",
                from_text=source,
                to_text=target,
                reason="extra_space",
            )
        )
        text = f"{text[:match.start()]}{target}{text[match.end():]}"


def _collapse_rules_in_match_order(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Ellipsis rules need to run before "." collapse, otherwise "..." would
    # become "。" before it has a chance to become "…".
    return sorted(rules, key=lambda rule: 0 if rule.get("to") == "…" else 1)


def _boundary_phrase_log_span(left: str, phrase: str, punctuation: str) -> tuple[str, str]:
    # The regex may capture a long prefix because Chinese text often has no
    # word boundary. Keep diagnostics short while preserving the actual edit.
    left_for_log = left[-8:] if len(left) > 8 else left
    return f"{left_for_log} {phrase}", f"{left_for_log}{punctuation}{phrase}"


def _enabled(value: object) -> bool:
    if isinstance(value, dict):
        return value.get("enabled", True) is not False
    return value is not False


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
