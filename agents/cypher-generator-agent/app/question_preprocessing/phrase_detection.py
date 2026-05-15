from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .text_cleaning import CleanTextResult


DEFAULT_PHRASE_SIGNALS_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "question_preprocessing"
    / "phrase_signals.yaml"
)


@dataclass(frozen=True)
class PhraseSpan:
    """第 2 步识别出的一个短语证据。start/end 基于 cleaned_question。"""

    text: str
    kind: str
    action: str
    start: int
    end: int
    rule_id: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "text": self.text,
            "kind": self.kind,
            "action": self.action,
            "start": self.start,
            "end": self.end,
            "rule_id": self.rule_id,
        }


@dataclass(frozen=True)
class PhraseDetectionResult:
    """短语识别输出，后续自我修正、背景剥离和准入判断都会消费它。"""

    cleaned_question: str
    phrase_spans: tuple[PhraseSpan, ...]
    scope_signals: dict[str, bool]
    reference_candidates: tuple[dict[str, str | int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "cleaned_question": self.cleaned_question,
            "phrase_spans": [span.to_dict() for span in self.phrase_spans],
            "scope_signals": self.scope_signals,
            "reference_candidates": list(self.reference_candidates),
        }


def detect_phrase_signals(
    cleaned_question: str | CleanTextResult,
    *,
    config: dict[str, Any] | None = None,
) -> PhraseDetectionResult:
    """识别语言功能短语；可以直接接收 clean_text() 的结果或其 cleaned_question。"""

    text = _coerce_cleaned_question(cleaned_question)
    phrase_config = config if config is not None else load_phrase_signals_config()

    candidates = _collect_phrase_candidates(text, phrase_config)
    phrase_spans = tuple(_resolve_overlaps(candidates, phrase_config))
    scope_signals = _build_scope_signals(phrase_spans, phrase_config)
    reference_candidates = tuple(_build_reference_candidates(text, phrase_spans))

    return PhraseDetectionResult(
        cleaned_question=text,
        phrase_spans=phrase_spans,
        scope_signals=scope_signals,
        reference_candidates=reference_candidates,
    )


def load_phrase_signals_config(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        return _load_default_phrase_signals_config()
    return _load_yaml_mapping(path)


@lru_cache(maxsize=1)
def _load_default_phrase_signals_config() -> dict[str, Any]:
    return _load_yaml_mapping(DEFAULT_PHRASE_SIGNALS_CONFIG_PATH)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"phrase signals config must be a mapping: {path}")
    return value


def _coerce_cleaned_question(value: str | CleanTextResult) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, CleanTextResult):
        return value.cleaned_question
    raise TypeError("cleaned_question must be a string or CleanTextResult")


def _collect_phrase_candidates(text: str, config: dict[str, Any]) -> list[PhraseSpan]:
    spans: list[PhraseSpan] = []
    phrase_groups = config.get("phrase_groups", {})
    if not isinstance(phrase_groups, dict):
        return spans

    for group in phrase_groups.values():
        if not isinstance(group, dict):
            continue
        if group.get("match_type", "phrase") != "phrase":
            continue

        kind = group.get("kind")
        action = group.get("action")
        if not isinstance(kind, str) or not isinstance(action, str):
            continue

        for item in _mapping_items(group.get("items")):
            phrase = item.get("text")
            rule_id = item.get("id")
            if not isinstance(phrase, str) or not phrase:
                continue
            if not isinstance(rule_id, str) or not rule_id:
                continue

            spans.extend(_find_phrase_occurrences(text, phrase, kind, action, rule_id))

    return spans


def _find_phrase_occurrences(
    text: str,
    phrase: str,
    kind: str,
    action: str,
    rule_id: str,
) -> list[PhraseSpan]:
    # 使用字符串精确匹配，避免把业务相关的大小写、标识符形态交给预处理猜。
    spans: list[PhraseSpan] = []
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return spans
        end = index + len(phrase)
        spans.append(
            PhraseSpan(
                text=phrase,
                kind=kind,
                action=action,
                start=index,
                end=end,
                rule_id=rule_id,
            )
        )
        start = index + 1


def _resolve_overlaps(spans: list[PhraseSpan], config: dict[str, Any]) -> list[PhraseSpan]:
    # 同一段文本可能命中多个短语，比如“查询一下”也包含“查询”。
    # 这里按 YAML 的 kind 优先级和“长短语优先”规则保留一个最有用的 span。
    if not spans:
        return []

    policy = config.get("matching_policy", {})
    priority = _overlap_priority(policy)
    prefer_longer = bool(policy.get("prefer_longer_span", True)) if isinstance(policy, dict) else True

    ranked = sorted(
        spans,
        key=lambda span: (
            priority.get(span.kind, len(priority)),
            -(span.end - span.start) if prefer_longer else 0,
            span.start,
            span.end,
            span.rule_id,
        ),
    )

    selected: list[PhraseSpan] = []
    occupied: list[tuple[int, int]] = []
    for span in ranked:
        if _overlaps_any(span.start, span.end, occupied):
            continue
        selected.append(span)
        occupied.append((span.start, span.end))

    return sorted(selected, key=lambda span: (span.start, span.end, span.kind, span.rule_id))


def _build_scope_signals(spans: tuple[PhraseSpan, ...], config: dict[str, Any]) -> dict[str, bool]:
    # scope_signals 是给后续阶段看的摘要：它只说明“是否出现过某类语言信号”。
    kinds = {span.kind for span in spans}
    rules = config.get("scope_signal_rules", {})
    if not isinstance(rules, dict):
        return {}

    signals: dict[str, bool] = {}
    for signal_name, rule in rules.items():
        if not isinstance(signal_name, str) or not isinstance(rule, dict):
            continue
        any_kind = _string_list(rule.get("any_kind"))
        signals[signal_name] = any(kind in kinds for kind in any_kind)
    return signals


def _build_reference_candidates(
    text: str,
    spans: tuple[PhraseSpan, ...],
    *,
    window_size: int = 24,
) -> list[dict[str, str | int]]:
    # 这里只保存指代词附近的文本窗口，方便后续阶段判断是否能在句内消解。
    # 不在这里判断“这个/他的”到底指向哪个业务对象。
    candidates: list[dict[str, str | int]] = []
    for span in spans:
        if span.kind not in {"reference_marker", "cross_turn_reference"}:
            continue
        candidates.append(
            {
                "marker_text": span.text,
                "marker_kind": span.kind,
                "marker_start": span.start,
                "marker_end": span.end,
                "local_window_before": text[max(0, span.start - window_size) : span.start],
                "local_window_after": text[span.end : span.end + window_size],
                "candidate_policy": "defer_to_reference_resolution",
            }
        )
    return candidates


def _overlap_priority(policy: object) -> dict[str, int]:
    if not isinstance(policy, dict):
        return {}
    return {kind: index for index, kind in enumerate(_string_list(policy.get("overlap_priority")))}


def _overlaps_any(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < used_end and end > used_start for used_start, used_end in ranges)


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
