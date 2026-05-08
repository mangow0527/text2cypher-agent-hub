from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from services.cypher_generator_agent.app.intent_recognition import IntentRecognitionResult


@dataclass(frozen=True)
class IntentEvalItem:
    id: str
    text: str
    primary_intent: str
    secondary_intent: str


@dataclass(frozen=True)
class IntentEvalFailure:
    id: str
    text: str
    expected_primary_intent: str
    expected_secondary_intent: str
    predicted_primary_intent: str | None
    predicted_secondary_intent: str | None
    source: str
    decision: str
    confidence: float

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "id": self.id,
            "text": self.text,
            "expected_primary_intent": self.expected_primary_intent,
            "expected_secondary_intent": self.expected_secondary_intent,
            "predicted_primary_intent": self.predicted_primary_intent,
            "predicted_secondary_intent": self.predicted_secondary_intent,
            "source": self.source,
            "decision": self.decision,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class IntentEvalSummary:
    total: int
    correct: int
    accuracy: float
    source_counts: dict[str, int]
    decision_counts: dict[str, int]
    confusion_pairs: list[dict[str, str | int]]
    failures: list[IntentEvalFailure]


@dataclass(frozen=True)
class IntentPressureItem:
    id: str
    text: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class IntentPressureResult:
    id: str
    text: str
    predicted_primary_intent: str | None
    predicted_secondary_intent: str | None
    source: str
    decision: str
    confidence: float
    metadata: dict[str, str]

    def to_dict(self) -> dict[str, str | float | None | dict[str, str]]:
        return {
            "id": self.id,
            "text": self.text,
            "predicted_primary_intent": self.predicted_primary_intent,
            "predicted_secondary_intent": self.predicted_secondary_intent,
            "source": self.source,
            "decision": self.decision,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class IntentPressureSummary:
    total: int
    source_counts: dict[str, int]
    decision_counts: dict[str, int]
    accepted_intent_counts: dict[str, int]
    results: list[IntentPressureResult]


class IntentRecognizerLike(Protocol):
    def recognize(self, question: str) -> IntentRecognitionResult:
        ...


def load_intent_eval_items(path: Path) -> list[IntentEvalItem]:
    items: list[IntentEvalItem] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = yaml.safe_load(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        item_id = str(payload.get("id") or f"case_{line_number:04d}")
        text = _required_payload_str(payload, "text", "question")
        primary_intent = _required_payload_str(payload, "primary_intent", "expected_primary_intent")
        secondary_intent = _required_payload_str(payload, "secondary_intent", "expected_secondary_intent")
        items.append(
            IntentEvalItem(
                id=item_id,
                text=text,
                primary_intent=primary_intent,
                secondary_intent=secondary_intent,
            )
        )
    return items


def load_intent_pressure_items(path: Path) -> list[IntentPressureItem]:
    items: list[IntentPressureItem] = []
    metadata_keys = ("difficulty", "query_type", "structure_family", "pass_fail", "failure_stage")
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = yaml.safe_load(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        item_id = str(payload.get("qa_id") or payload.get("id") or f"case_{line_number:04d}")
        text = _required_payload_str(payload, "question", "text", "question_canonical_zh")
        metadata = {
            key: str(payload[key])
            for key in metadata_keys
            if key in payload and payload[key] is not None
        }
        items.append(IntentPressureItem(id=item_id, text=text, metadata=metadata))
    return items


def evaluate_intent_recognizer(
    items: list[IntentEvalItem],
    recognizer: IntentRecognizerLike,
) -> IntentEvalSummary:
    failures: list[IntentEvalFailure] = []
    source_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    confusion_counter: Counter[tuple[str, str]] = Counter()
    correct = 0

    for item in items:
        result = recognizer.recognize(item.text)
        source_counts[result.source] += 1
        decision_counts[result.decision] += 1
        expected = (item.primary_intent, item.secondary_intent)
        predicted = (result.primary_intent, result.secondary_intent)
        if result.decision == "accept" and predicted == expected:
            correct += 1
            continue

        expected_key = f"{item.primary_intent}.{item.secondary_intent}"
        predicted_key = f"{result.primary_intent}.{result.secondary_intent}"
        confusion_counter[(expected_key, predicted_key)] += 1
        failures.append(
            IntentEvalFailure(
                id=item.id,
                text=item.text,
                expected_primary_intent=item.primary_intent,
                expected_secondary_intent=item.secondary_intent,
                predicted_primary_intent=result.primary_intent,
                predicted_secondary_intent=result.secondary_intent,
                source=result.source,
                decision=result.decision,
                confidence=result.confidence,
            )
        )

    total = len(items)
    return IntentEvalSummary(
        total=total,
        correct=correct,
        accuracy=round(correct / total, 4) if total else 0.0,
        source_counts=dict(source_counts),
        decision_counts=dict(decision_counts),
        confusion_pairs=[
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in confusion_counter.most_common()
        ],
        failures=failures,
    )


def summarize_intent_pressure(
    items: list[IntentPressureItem],
    recognizer: IntentRecognizerLike,
) -> IntentPressureSummary:
    source_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    accepted_intent_counts: Counter[str] = Counter()
    results: list[IntentPressureResult] = []

    for item in items:
        result = recognizer.recognize(item.text)
        source_counts[result.source] += 1
        decision_counts[result.decision] += 1
        if result.decision == "accept" and result.primary_intent and result.secondary_intent:
            accepted_intent_counts[f"{result.primary_intent}.{result.secondary_intent}"] += 1
        results.append(
            IntentPressureResult(
                id=item.id,
                text=item.text,
                predicted_primary_intent=result.primary_intent,
                predicted_secondary_intent=result.secondary_intent,
                source=result.source,
                decision=result.decision,
                confidence=result.confidence,
                metadata=item.metadata,
            )
        )

    return IntentPressureSummary(
        total=len(items),
        source_counts=dict(source_counts),
        decision_counts=dict(decision_counts),
        accepted_intent_counts=dict(accepted_intent_counts),
        results=results,
    )


def _required_payload_str(payload: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"missing required string: {'/'.join(keys)}")
