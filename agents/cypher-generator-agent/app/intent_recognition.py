from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Literal, Protocol

import yaml

from . import resource_paths


IntentSource = Literal["rule", "embedding", "llm"]
IntentDecision = Literal["accept", "fallback_embedding", "fallback_llm", "clarify"]


@dataclass(frozen=True)
class IntentRecognitionResult:
    primary_intent: str | None
    secondary_intent: str | None
    confidence: float
    source: IntentSource
    decision: IntentDecision

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "primary_intent": self.primary_intent,
            "secondary_intent": self.secondary_intent,
            "confidence": self.confidence,
            "source": self.source,
            "decision": self.decision,
        }


@dataclass(frozen=True)
class IntentRule:
    rule_id: str
    primary_intent: str
    secondary_intent: str
    confidence: float
    include_any: tuple[str, ...]
    include_any_secondary: tuple[str, ...]
    exclude_any: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> "IntentRule":
        return cls(
            rule_id=_required_str(value, "rule_id"),
            primary_intent=_required_str(value, "primary_intent"),
            secondary_intent=_required_str(value, "secondary_intent"),
            confidence=float(value.get("confidence", 0.0)),
            include_any=_string_tuple(value.get("include_any")),
            include_any_secondary=_string_tuple(value.get("include_any_secondary")),
            exclude_any=_string_tuple(value.get("exclude_any")),
        )

    def matches(self, question: str) -> bool:
        if _contains_any(question, self.exclude_any):
            return False
        if not _contains_any(question, self.include_any):
            return False
        if self.include_any_secondary and not _contains_any(question, self.include_any_secondary):
            return False
        return True


@dataclass(frozen=True)
class QueryStructuralFeatures:
    has_limit: bool
    has_order_signal: bool
    has_filter_condition: bool
    has_projection_fields: bool
    has_path_signal: bool
    has_relation_signal: bool
    has_group_signal: bool
    has_aggregation_signal: bool
    has_numeric_aggregation_signal: bool
    has_existence_signal: bool
    has_ratio_signal: bool
    has_set_signal: bool
    has_time_signal: bool
    has_comparison_signal: bool
    projection_field_count: int


class IntentCandidateGate:
    def allows(self, question: str, candidate: "IntentEmbeddingCandidate") -> bool:
        features = extract_query_structural_features(question)
        primary_intent = candidate.primary_intent
        secondary_intent = candidate.secondary_intent

        if primary_intent == "ranking_query":
            return self._allows_ranking(features)
        if primary_intent == "metric_query":
            return self._allows_metric(features, secondary_intent)
        if primary_intent == "breakdown_query":
            return self._allows_breakdown(features)
        if primary_intent == "relationship_path_query":
            return self._allows_path(features, secondary_intent)
        if secondary_intent == "related_record_query":
            return self._allows_related_record(features)
        if secondary_intent == "entity_list_query":
            return not features.has_projection_fields
        if secondary_intent == "entity_detail_query":
            return not (features.has_projection_fields and not _contains_any(question, ("详细", "详情", "完整", "信息")))
        if secondary_intent == "attribute_projection_query":
            return features.has_projection_fields or not _has_strong_structural_signal(features)
        if primary_intent == "existence_query":
            return features.has_existence_signal or not _has_strong_structural_signal(features)
        if primary_intent == "composition_query":
            return features.has_ratio_signal or not _has_strong_structural_signal(features)
        if primary_intent == "set_operation_query":
            return self._allows_set_operation(question, features, secondary_intent)
        if primary_intent == "trend_query":
            return features.has_time_signal or not _has_strong_structural_signal(features)
        if primary_intent == "comparison_query":
            return features.has_comparison_signal or not _has_strong_structural_signal(features)
        return True

    def _allows_ranking(self, features: QueryStructuralFeatures) -> bool:
        if features.has_limit and not features.has_order_signal:
            return False
        return features.has_order_signal or not _has_strong_structural_signal(features)

    def _allows_metric(self, features: QueryStructuralFeatures, secondary_intent: str) -> bool:
        if secondary_intent == "numeric_metric_query":
            if features.has_filter_condition and not features.has_numeric_aggregation_signal:
                return False
            return features.has_numeric_aggregation_signal or not _has_strong_structural_signal(features)
        if secondary_intent in {"count_metric_query", "multi_metric_query"}:
            return features.has_aggregation_signal or not _has_strong_structural_signal(features)
        return True

    def _allows_breakdown(self, features: QueryStructuralFeatures) -> bool:
        if features.has_path_signal and not features.has_group_signal:
            return False
        if features.has_group_signal and features.has_aggregation_signal:
            return True
        return not _has_strong_structural_signal(features)

    def _allows_path(self, features: QueryStructuralFeatures, secondary_intent: str) -> bool:
        if secondary_intent == "topology_subgraph_query":
            return features.has_path_signal or not _has_strong_structural_signal(features)
        return features.has_path_signal or not _has_strong_structural_signal(features)

    def _allows_related_record(self, features: QueryStructuralFeatures) -> bool:
        if features.has_path_signal:
            return False
        return features.has_relation_signal or not _has_strong_structural_signal(features)

    def _allows_set_operation(
        self,
        question: str,
        features: QueryStructuralFeatures,
        secondary_intent: str,
    ) -> bool:
        if features.has_set_signal:
            return True
        if secondary_intent == "set_union_query":
            return _contains_any(question, ("所有", "全部")) and _contains_any(question, ("两个", "多个", "和"))
        return not _has_strong_structural_signal(features)


class RuleEligibilityGate:
    breakdown_intents = {
        "single_dimension_breakdown_query",
        "multi_dimension_breakdown_query",
        "multi_metric_breakdown_query",
    }
    ranking_intents = {
        "attribute_ranking_query",
        "metric_ranking_query",
        "derived_metric_ranking_query",
    }
    path_intents = {
        "path_trace_query",
        "reachable_entity_query",
        "path_enumeration_query",
    }
    relation_terms = (
        "使用",
        "连接",
        "关联",
        "拥有",
        "经过",
        "穿过",
        "对应",
        "可达",
        "到达",
        "源端",
        "目的端",
        "所连接",
        "所关联",
    )
    condition_terms = (
        "大于",
        "小于",
        "超过",
        "至少",
        "等于",
        "状态为",
        "配置了",
        "存在",
        "为 down",
        "为down",
        "为1",
        "限制",
    )
    chain_terms = ("且", "并且", "同时", "以及", "其", "该", "作为")
    group_terms = ("按", "分组", "每个", "每种", "每类", "各")
    metric_terms = ("数量", "总数", "个数", "多少")
    multi_metric_terms = ("以及", "同时", "数量和", "总数和", "和平均", "和最大", "及其中", "其中")

    def is_eligible(self, question: str, secondary_intent: str) -> bool:
        if question.lstrip().startswith("{"):
            return False

        relation_count = _term_count(question, self.relation_terms)
        condition_count = _term_count(question, self.condition_terms)
        chain_count = _term_count(question, self.chain_terms)

        if secondary_intent in self.breakdown_intents:
            if self._matches_stable_breakdown_template(question, secondary_intent):
                return True
            return not (
                relation_count >= 2
                or (relation_count >= 1 and condition_count >= 1)
                or (relation_count >= 1 and chain_count >= 2)
            )
        if secondary_intent in self.ranking_intents:
            return not (relation_count >= 2 or (relation_count >= 1 and condition_count >= 1))
        if secondary_intent in self.path_intents:
            return not (
                (relation_count >= 2 and condition_count >= 1)
                or (relation_count >= 2 and chain_count >= 1)
            )
        return True

    def _matches_stable_breakdown_template(self, question: str, secondary_intent: str) -> bool:
        if _contains_any(question, self.condition_terms):
            return False
        if secondary_intent != "multi_metric_breakdown_query":
            return False
        return (
            _contains_any(question, self.group_terms)
            and _contains_any(question, self.metric_terms)
            and _contains_any(question, self.multi_metric_terms)
        )


def extract_query_structural_features(question: str) -> QueryStructuralFeatures:
    has_limit = _has_limit_signal(question)
    has_order_signal = _has_order_signal(question)
    has_filter_condition = _has_filter_condition(question)
    projection_field_count = _projection_field_count(question)
    has_projection_fields = _has_projection_fields(
        question,
        projection_field_count=projection_field_count,
        has_filter_condition=has_filter_condition,
    )
    has_group_signal = _has_group_signal(question)
    has_aggregation_signal = _contains_any(
        question,
        ("统计", "数量", "总数", "个数", "多少", "平均", "最大", "最小", "总和", "求和", "占比", "比例"),
    )
    has_numeric_aggregation_signal = _contains_any(
        question,
        ("平均", "最大", "最小", "总和", "求和", "最高", "最低", "峰值"),
    )
    has_path_signal = _contains_any(
        question,
        ("路径", "经过", "途经", "可达", "到达", "构成路径", "构成的路径", "关系链", "链路路径", "经"),
    )
    has_relation_signal = _contains_any(
        question,
        ("使用", "连接", "关联", "对应", "源端", "目的端", "拥有", "所连接", "所关联", "下挂", "挂着"),
    )
    has_existence_signal = _contains_any(question, ("是否", "有没有", "是否存在", "存在吗", "存在么", "满足吗", "是否满足"))
    has_ratio_signal = _contains_any(question, ("占比", "比例", "构成", "覆盖率", "利用率"))
    has_set_signal = _contains_any(question, ("共同", "都", "未被", "未使用", "没有使用", "集合", "交集", "并集", "差集", "合并", "同时属于", "任意"))
    has_time_signal = _contains_any(
        question,
        ("按天", "按小时", "按月", "按周", "每天", "每小时", "每月", "最近", "历史", "趋势", "变化", "同比", "环比", "上月", "本月"),
    )
    has_comparison_signal = _contains_any(question, ("比较", "对比", "相同", "一致", "不同", "差异", "更高", "更低", "更多", "更少", "谁", "哪个"))
    return QueryStructuralFeatures(
        has_limit=has_limit,
        has_order_signal=has_order_signal,
        has_filter_condition=has_filter_condition,
        has_projection_fields=has_projection_fields,
        has_path_signal=has_path_signal,
        has_relation_signal=has_relation_signal,
        has_group_signal=has_group_signal,
        has_aggregation_signal=has_aggregation_signal,
        has_numeric_aggregation_signal=has_numeric_aggregation_signal,
        has_existence_signal=has_existence_signal,
        has_ratio_signal=has_ratio_signal,
        has_set_signal=has_set_signal,
        has_time_signal=has_time_signal,
        has_comparison_signal=has_comparison_signal,
        projection_field_count=projection_field_count,
    )


@dataclass(frozen=True)
class IntentEmbeddingSample:
    id: str
    primary_intent: str
    secondary_intent: str
    text: str

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> "IntentEmbeddingSample":
        return cls(
            id=_required_str(value, "id"),
            primary_intent=_required_str(value, "primary_intent"),
            secondary_intent=_required_str(value, "secondary_intent"),
            text=_required_str(value, "text"),
        )


@dataclass(frozen=True)
class IntentEmbeddingCandidate:
    sample_id: str
    primary_intent: str
    secondary_intent: str
    sample_text: str
    score: float

    def to_dict(self) -> dict[str, str | float]:
        return {
            "sample_id": self.sample_id,
            "primary_intent": self.primary_intent,
            "secondary_intent": self.secondary_intent,
            "sample_text": self.sample_text,
            "score": self.score,
        }


@dataclass(frozen=True)
class EmbeddingRecognitionDiagnostic:
    question: str
    candidates: list[IntentEmbeddingCandidate]
    top_score: float
    margin: float | None
    consensus_count: int
    consensus_min_count: int
    accepted: bool
    decision: IntentDecision
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "top_score": self.top_score,
            "margin": self.margin,
            "consensus_count": self.consensus_count,
            "consensus_min_count": self.consensus_min_count,
            "accepted": self.accepted,
            "decision": self.decision,
            "reason": self.reason,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


class TextEmbedder(Protocol):
    def embed(self, text: str) -> tuple[float, ...]:
        ...


class EmbeddingStore(Protocol):
    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: str | None = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        ...


class InMemoryEmbeddingStore:
    def __init__(self, *, samples: list[IntentEmbeddingSample], embedder: TextEmbedder) -> None:
        self.samples = samples
        self.embedder = embedder
        self._sample_vectors = [(sample, embedder.embed(sample.text)) for sample in samples]

    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: str | None = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        scored_samples = sorted(
            (
                (sample, _cosine_similarity(query_vector, sample_vector))
                for sample, sample_vector in self._sample_vectors
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        return scored_samples[:top_k]


class JsonlEmbeddingStore:
    def __init__(self, *, sample_vectors: list[tuple[IntentEmbeddingSample, tuple[float, ...]]]) -> None:
        self._sample_vectors = sample_vectors
        self.samples = [sample for sample, _vector in sample_vectors]

    @classmethod
    def from_path(cls, path: Path) -> "JsonlEmbeddingStore":
        sample_vectors: list[tuple[IntentEmbeddingSample, tuple[float, ...]]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = yaml.safe_load(line)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            vector = payload.get("vector")
            if not isinstance(vector, list) or not vector:
                raise ValueError(f"{path}:{line_number} missing vector list")
            sample_vectors.append(
                (
                    IntentEmbeddingSample.from_mapping(payload),
                    tuple(float(value) for value in vector),
                )
            )
        return cls(sample_vectors=sample_vectors)

    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        top_k: int,
        query_text: str | None = None,
    ) -> list[tuple[IntentEmbeddingSample, float]]:
        scored_samples = sorted(
            (
                (sample, _cosine_similarity(query_vector, sample_vector))
                for sample, sample_vector in self._sample_vectors
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        return scored_samples[:top_k]


def write_embedding_index(
    *,
    samples: list[IntentEmbeddingSample],
    embedder: TextEmbedder,
    output_path: Path,
    provider: str,
    model_name: str | None = None,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for sample in samples:
        payload = {
            "id": sample.id,
            "primary_intent": sample.primary_intent,
            "secondary_intent": sample.secondary_intent,
            "text": sample.text,
            "embedding_provider": provider,
            "embedding_model": model_name,
            "vector": list(embedder.embed(sample.text)),
        }
        lines.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


class RuleBasedIntentRecognizer:
    def __init__(
        self,
        *,
        rules: list[IntentRule],
        valid_intents: set[tuple[str, str]],
        source: IntentSource = "rule",
        decision: IntentDecision = "accept",
        no_match_decision: IntentDecision = "fallback_embedding",
        conflict_decision: IntentDecision = "fallback_llm",
        eligibility_gate: RuleEligibilityGate | None = None,
    ) -> None:
        self.rules = rules
        self.valid_intents = valid_intents
        self.source = source
        self.decision = decision
        self.no_match_decision = no_match_decision
        self.conflict_decision = conflict_decision
        self.eligibility_gate = eligibility_gate or RuleEligibilityGate()
        self._validate_rules()

    @classmethod
    def from_files(cls, *, taxonomy_path: Path, rules_path: Path) -> "RuleBasedIntentRecognizer":
        taxonomy = _load_yaml_mapping(taxonomy_path)
        rules_doc = _load_yaml_mapping(rules_path)
        defaults = rules_doc.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
        return cls(
            rules=[IntentRule.from_mapping(rule) for rule in _required_list(rules_doc, "rules")],
            valid_intents=_extract_valid_intents(taxonomy),
            source=str(defaults.get("source", "rule")),  # type: ignore[arg-type]
            decision=str(defaults.get("decision", "accept")),  # type: ignore[arg-type]
            no_match_decision=str(defaults.get("no_match_decision", "fallback_embedding")),  # type: ignore[arg-type]
            conflict_decision=str(defaults.get("conflict_decision", "fallback_llm")),  # type: ignore[arg-type]
        )

    def recognize(self, question: str) -> IntentRecognitionResult:
        matched_rules = [rule for rule in self.rules if rule.matches(question)]
        if not matched_rules:
            return IntentRecognitionResult(
                primary_intent=None,
                secondary_intent=None,
                confidence=0.0,
                source=self.source,
                decision=self.no_match_decision,
            )

        matched_rules.sort(key=lambda rule: rule.confidence, reverse=True)
        best_confidence = matched_rules[0].confidence
        top_rules = [rule for rule in matched_rules if rule.confidence == best_confidence]
        top_intents = {(rule.primary_intent, rule.secondary_intent) for rule in top_rules}
        if len(top_intents) > 1:
            return IntentRecognitionResult(
                primary_intent=None,
                secondary_intent=None,
                confidence=best_confidence,
                source=self.source,
                decision=self.conflict_decision,
            )

        best_rule = top_rules[0]
        if not self.eligibility_gate.is_eligible(question, best_rule.secondary_intent):
            return IntentRecognitionResult(
                primary_intent=None,
                secondary_intent=None,
                confidence=best_rule.confidence,
                source=self.source,
                decision=self.no_match_decision,
            )
        return IntentRecognitionResult(
            primary_intent=best_rule.primary_intent,
            secondary_intent=best_rule.secondary_intent,
            confidence=best_rule.confidence,
            source=self.source,
            decision=self.decision,
        )

    def _validate_rules(self) -> None:
        invalid_rules = [
            rule.rule_id
            for rule in self.rules
            if (rule.primary_intent, rule.secondary_intent) not in self.valid_intents
        ]
        if invalid_rules:
            raise ValueError(f"intent rules reference unknown intents: {', '.join(invalid_rules)}")


class LocalTextEmbedder:
    def __init__(self, *, dimensions: int = 128) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimensions
        normalized_text = "".join(text.lower().split())
        tokens = _text_features(normalized_text)
        for token in tokens:
            vector[_stable_hash(token) % self.dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


class SentenceTransformerTextEmbedder:
    def __init__(
        self,
        *,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        model: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self._model = model

    def embed(self, text: str) -> tuple[float, ...]:
        raw_vector = self._get_model().encode(text, normalize_embeddings=True)
        if hasattr(raw_vector, "tolist"):
            raw_vector = raw_vector.tolist()
        return tuple(float(value) for value in raw_vector)

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = self._load_model()
        return self._model

    def _load_model(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for provider='sentence_transformer'. "
                "Install it separately before using this embedder."
            ) from exc
        return SentenceTransformer(self.model_name)


def build_text_embedder(
    *,
    provider: str = "local_hash",
    model_name: str | None = None,
    dimensions: int = 128,
) -> TextEmbedder:
    normalized_provider = provider.strip().lower().replace("-", "_")
    if normalized_provider in {"local", "local_hash", "hash"}:
        return LocalTextEmbedder(dimensions=dimensions)
    if normalized_provider in {"sentence_transformer", "sentence_transformers"}:
        return SentenceTransformerTextEmbedder(
            model_name=model_name or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    raise ValueError(f"unknown text embedder provider: {provider}")


class EmbeddingIntentRecognizer:
    def __init__(
        self,
        *,
        samples: list[IntentEmbeddingSample],
        valid_intents: set[tuple[str, str]],
        embedder: TextEmbedder | None = None,
        store: EmbeddingStore | None = None,
        accept_threshold: float = 0.35,
        margin_threshold: float = 0.02,
        default_top_k: int = 5,
        consensus_top_k: int = 3,
        consensus_min_count: int = 2,
        candidate_gate: IntentCandidateGate | None = None,
    ) -> None:
        self.samples = samples
        self.valid_intents = valid_intents
        self.embedder = embedder or LocalTextEmbedder()
        self.accept_threshold = accept_threshold
        self.margin_threshold = margin_threshold
        self.default_top_k = default_top_k
        self.consensus_top_k = consensus_top_k
        self.consensus_min_count = consensus_min_count
        self.candidate_gate = candidate_gate or IntentCandidateGate()
        self._validate_samples()
        self.store = store or InMemoryEmbeddingStore(samples=self.samples, embedder=self.embedder)

    @classmethod
    def from_samples(
        cls,
        samples: list[dict[str, object]],
        *,
        valid_intents: set[tuple[str, str]],
        embedder: TextEmbedder | None = None,
        store: EmbeddingStore | None = None,
        accept_threshold: float = 0.35,
        margin_threshold: float = 0.02,
        default_top_k: int = 5,
        consensus_top_k: int = 3,
        consensus_min_count: int = 2,
        candidate_gate: IntentCandidateGate | None = None,
    ) -> "EmbeddingIntentRecognizer":
        return cls(
            samples=[IntentEmbeddingSample.from_mapping(sample) for sample in samples],
            valid_intents=valid_intents,
            embedder=embedder,
            store=store,
            accept_threshold=accept_threshold,
            margin_threshold=margin_threshold,
            default_top_k=default_top_k,
            consensus_top_k=consensus_top_k,
            consensus_min_count=consensus_min_count,
            candidate_gate=candidate_gate,
        )

    @classmethod
    def from_files(
        cls,
        *,
        taxonomy_path: Path,
        corpus_path: Path,
        embedder: TextEmbedder | None = None,
        store: EmbeddingStore | None = None,
        accept_threshold: float = 0.35,
        margin_threshold: float = 0.02,
        default_top_k: int = 5,
        consensus_top_k: int = 3,
        consensus_min_count: int = 2,
        candidate_gate: IntentCandidateGate | None = None,
    ) -> "EmbeddingIntentRecognizer":
        taxonomy = _load_yaml_mapping(taxonomy_path)
        return cls.from_samples(
            _load_jsonl_mappings(corpus_path),
            valid_intents=_extract_valid_intents(taxonomy),
            embedder=embedder,
            store=store,
            accept_threshold=accept_threshold,
            margin_threshold=margin_threshold,
            default_top_k=default_top_k,
            consensus_top_k=consensus_top_k,
            consensus_min_count=consensus_min_count,
            candidate_gate=candidate_gate,
        )

    @classmethod
    def from_index_file(
        cls,
        *,
        taxonomy_path: Path,
        index_path: Path,
        embedder: TextEmbedder | None = None,
        accept_threshold: float = 0.35,
        margin_threshold: float = 0.02,
        default_top_k: int = 5,
        consensus_top_k: int = 3,
        consensus_min_count: int = 2,
        candidate_gate: IntentCandidateGate | None = None,
    ) -> "EmbeddingIntentRecognizer":
        taxonomy = _load_yaml_mapping(taxonomy_path)
        store = JsonlEmbeddingStore.from_path(index_path)
        return cls(
            samples=store.samples,
            valid_intents=_extract_valid_intents(taxonomy),
            embedder=embedder,
            store=store,
            accept_threshold=accept_threshold,
            margin_threshold=margin_threshold,
            default_top_k=default_top_k,
            consensus_top_k=consensus_top_k,
            consensus_min_count=consensus_min_count,
            candidate_gate=candidate_gate,
        )

    def retrieve_candidates(self, question: str, *, top_k: int | None = None) -> list[IntentEmbeddingCandidate]:
        query_vector = self.embedder.embed(question)
        limit = top_k or self.default_top_k
        scored_samples = self.store.search(query_vector, top_k=limit, query_text=question)
        return [
            IntentEmbeddingCandidate(
                sample_id=sample.id,
                primary_intent=sample.primary_intent,
                secondary_intent=sample.secondary_intent,
                sample_text=sample.text,
                score=round(score, 4),
            )
            for sample, score in scored_samples
            if (sample.primary_intent, sample.secondary_intent) in self.valid_intents
        ]

    def recognize(self, question: str) -> IntentRecognitionResult:
        diagnostic = self.diagnose(question)
        if not diagnostic.candidates:
            return IntentRecognitionResult(
                primary_intent=None,
                secondary_intent=None,
                confidence=0.0,
                source="embedding",
                decision="fallback_llm",
            )
        best_candidate = diagnostic.candidates[0]
        confidence = best_candidate.score
        if not diagnostic.accepted:
            return IntentRecognitionResult(
                primary_intent=None,
                secondary_intent=None,
                confidence=confidence,
                source="embedding",
                decision="fallback_llm",
            )
        return IntentRecognitionResult(
            primary_intent=best_candidate.primary_intent,
            secondary_intent=best_candidate.secondary_intent,
            confidence=confidence,
            source="embedding",
            decision="accept",
        )

    def diagnose(self, question: str, *, top_k: int | None = None) -> EmbeddingRecognitionDiagnostic:
        raw_candidates = self.retrieve_candidates(question, top_k=top_k)
        if not raw_candidates:
            return EmbeddingRecognitionDiagnostic(
                question=question,
                candidates=[],
                top_score=0.0,
                margin=None,
                consensus_count=0,
                consensus_min_count=self.consensus_min_count,
                accepted=False,
                decision="fallback_llm",
                reason="no_candidates",
            )
        candidates = [
            candidate
            for candidate in raw_candidates
            if self.candidate_gate.allows(question, candidate)
        ]
        if not candidates:
            return EmbeddingRecognitionDiagnostic(
                question=question,
                candidates=raw_candidates,
                top_score=raw_candidates[0].score,
                margin=round(raw_candidates[0].score - raw_candidates[1].score, 4)
                if len(raw_candidates) > 1
                else None,
                consensus_count=0,
                consensus_min_count=self.consensus_min_count,
                accepted=False,
                decision="fallback_llm",
                reason="structural_gate_rejected",
            )

        top_score = candidates[0].score
        margin = round(candidates[0].score - candidates[1].score, 4) if len(candidates) > 1 else None
        consensus_count = self._top_intent_support_count(candidates)
        if top_score < self.accept_threshold:
            accepted = False
            reason = "below_threshold"
        elif not self._has_clear_candidate(candidates):
            accepted = False
            reason = "ambiguous_candidates"
        else:
            accepted = True
            reason = "accepted"

        return EmbeddingRecognitionDiagnostic(
            question=question,
            candidates=candidates,
            top_score=top_score,
            margin=margin,
            consensus_count=consensus_count,
            consensus_min_count=self.consensus_min_count,
            accepted=accepted,
            decision="accept" if accepted else "fallback_llm",
            reason=reason,
        )

    def _validate_samples(self) -> None:
        invalid_samples = [
            sample.id
            for sample in self.samples
            if (sample.primary_intent, sample.secondary_intent) not in self.valid_intents
        ]
        if invalid_samples:
            raise ValueError(f"embedding samples reference unknown intents: {', '.join(invalid_samples)}")

    def _has_clear_candidate(self, candidates: list[IntentEmbeddingCandidate]) -> bool:
        if len(candidates) == 1 or self.margin_threshold <= 0:
            return True
        margin = candidates[0].score - candidates[1].score
        if margin >= self.margin_threshold:
            return True
        return self._top_intent_support_count(candidates) >= self.consensus_min_count

    def _top_intent_support_count(self, candidates: list[IntentEmbeddingCandidate]) -> int:
        if not candidates:
            return 0
        top_intent = (candidates[0].primary_intent, candidates[0].secondary_intent)
        return sum(
            1
            for candidate in candidates[: self.consensus_top_k]
            if (candidate.primary_intent, candidate.secondary_intent) == top_intent
        )


class HybridIntentRecognizer:
    def __init__(
        self,
        *,
        rule_recognizer: RuleBasedIntentRecognizer,
        embedding_recognizer: EmbeddingIntentRecognizer,
    ) -> None:
        self.rule_recognizer = rule_recognizer
        self.embedding_recognizer = embedding_recognizer

    def recognize(self, question: str) -> IntentRecognitionResult:
        rule_result = self.rule_recognizer.recognize(question)
        if rule_result.decision == "accept":
            return rule_result
        return self.embedding_recognizer.recognize(question)


@lru_cache(maxsize=1)
def get_rule_based_intent_recognizer() -> RuleBasedIntentRecognizer:
    return RuleBasedIntentRecognizer.from_files(
        taxonomy_path=resource_paths.intent_taxonomy_path(),
        rules_path=resource_paths.intent_rules_path(),
    )


@lru_cache(maxsize=1)
def get_hybrid_intent_recognizer() -> HybridIntentRecognizer:
    embedding_store = os.getenv("NL2CYPHER_INTENT_EMBEDDING_STORE", "local").strip().lower()
    embedding_index = os.getenv("NL2CYPHER_INTENT_EMBEDDING_INDEX")
    embedder = build_text_embedder(
        provider=os.getenv("NL2CYPHER_INTENT_EMBEDDER_PROVIDER", "local_hash"),
        model_name=os.getenv("NL2CYPHER_INTENT_EMBEDDING_MODEL"),
        dimensions=_env_int("NL2CYPHER_INTENT_LOCAL_EMBEDDING_DIMENSIONS", 128),
    )
    embedding_kwargs = {
        "embedder": embedder,
        "accept_threshold": _env_float("NL2CYPHER_INTENT_ACCEPT_THRESHOLD", 0.35),
        "margin_threshold": _env_float("NL2CYPHER_INTENT_MARGIN_THRESHOLD", 0.02),
        "consensus_top_k": _env_int("NL2CYPHER_INTENT_CONSENSUS_TOP_K", 3),
        "consensus_min_count": _env_int("NL2CYPHER_INTENT_CONSENSUS_MIN_COUNT", 2),
    }
    if embedding_store in {"rag", "rag_vector", "rag_vector_store"}:
        from .intent_vector_store import FallbackEmbeddingStore, RagIntentEmbeddingStore

        rag_store = RagIntentEmbeddingStore(
            base_url=_env_first(
                "NL2CYPHER_INTENT_RAG_SERVICE_URL",
                "CYPHER_GENERATOR_AGENT_RAG_SERVICE_URL",
                default="http://127.0.0.1:8004",
            ),
            collection=os.getenv("NL2CYPHER_INTENT_RAG_COLLECTION", "nl2cypher_intent_examples_v1"),
            endpoint_path=os.getenv("NL2CYPHER_INTENT_RAG_SEARCH_PATH", "/api/v1/intent/search"),
            taxonomy_version=os.getenv("NL2CYPHER_INTENT_TAXONOMY_VERSION"),
            timeout_seconds=float(
                _env_first(
                    "NL2CYPHER_INTENT_RAG_TIMEOUT_SECONDS",
                    "CYPHER_GENERATOR_AGENT_RAG_REQUEST_TIMEOUT_SECONDS",
                    default="60",
                )
            ),
            include_query_vector=_env_bool("NL2CYPHER_INTENT_RAG_INCLUDE_QUERY_VECTOR", False),
        )
        store: EmbeddingStore = rag_store
        if _env_bool("NL2CYPHER_INTENT_FALLBACK_TO_LOCAL", True):
            store = FallbackEmbeddingStore(
                primary=rag_store,
                fallback=_build_local_embedding_store(
                    embedder=embedder,
                    embedding_index=embedding_index,
                ),
            )
        embedding_recognizer = EmbeddingIntentRecognizer(
            samples=[],
            valid_intents=_extract_valid_intents(_load_yaml_mapping(resource_paths.intent_taxonomy_path())),
            store=store,
            **embedding_kwargs,
        )
    elif embedding_index:
        embedding_recognizer = EmbeddingIntentRecognizer.from_index_file(
            taxonomy_path=resource_paths.intent_taxonomy_path(),
            index_path=Path(embedding_index),
            **embedding_kwargs,
        )
    else:
        embedding_recognizer = EmbeddingIntentRecognizer.from_files(
            taxonomy_path=resource_paths.intent_taxonomy_path(),
            corpus_path=resource_paths.intent_embedding_corpus_path(),
            **embedding_kwargs,
        )
    return HybridIntentRecognizer(
        rule_recognizer=get_rule_based_intent_recognizer(),
        embedding_recognizer=embedding_recognizer,
    )


def _build_local_embedding_store(
    *,
    embedder: TextEmbedder,
    embedding_index: str | None,
) -> EmbeddingStore:
    if embedding_index:
        return JsonlEmbeddingStore.from_path(Path(embedding_index))
    samples = [
        IntentEmbeddingSample.from_mapping(sample)
        for sample in _load_jsonl_mappings(resource_paths.intent_embedding_corpus_path())
    ]
    return InMemoryEmbeddingStore(samples=samples, embedder=embedder)


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def _load_jsonl_mappings(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = yaml.safe_load(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        records.append(payload)
    return records


def _extract_valid_intents(taxonomy: dict[str, object]) -> set[tuple[str, str]]:
    valid_intents: set[tuple[str, str]] = set()
    for primary in _required_list(taxonomy, "intents"):
        if not isinstance(primary, dict):
            continue
        primary_intent = _required_str(primary, "primary_intent")
        for secondary in _required_list(primary, "secondary_intents"):
            if not isinstance(secondary, dict):
                continue
            valid_intents.add((primary_intent, _required_str(secondary, "secondary_intent")))
    return valid_intents


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _has_limit_signal(text: str) -> bool:
    return bool(
        re.search(r"前\s*\d+\s*条|前\s*\d+\s*个|top\s*\d+|最多\s*返回|限制.*返回|只显示", text, re.IGNORECASE)
    )


def _has_order_signal(text: str) -> bool:
    if re.search(r"最多\s*返回|限制.*最多|只显示\s*前", text):
        limit_only_text = re.sub(r"最多\s*返回|限制.*最多|只显示\s*前", "", text)
    else:
        limit_only_text = text
    if re.search(r"升序|降序|排序|排列|排名|排在|从高到低|从低到高|从长到短|从短到长|从大到小|从小到大", limit_only_text):
        return True
    return bool(re.search(r"最高|最低|最大|最小|最多的|最少的|数量最多|数量最少|率最高|率最低", limit_only_text))


def _has_filter_condition(text: str) -> bool:
    if re.search(r"(ID|编号|名称|类型|状态|带宽|时延|延迟|长度|速率|厂商|版本)\s*(为|是|等于|=|大于|小于|不等于|不是)", text):
        return True
    return _contains_any(text, ("大于等于", "小于等于", "至少", "超过", "在'sample'到", "在 sample 到"))


def _has_group_signal(text: str) -> bool:
    if _contains_any(text, ("分组", "每个", "每种", "每类", "各", "分别统计", "分布")):
        return True
    if re.search(r"按[^，。？?]*(统计|分组|汇总|分别)", text):
        return True
    return False


def _projection_field_count(text: str) -> int:
    field_terms = (
        "ID",
        "编号",
        "标识",
        "名称",
        "状态",
        "管理状态",
        "带宽",
        "延迟",
        "时延",
        "类型",
        "厂商",
        "软件版本",
        "版本",
        "位置",
        "速率",
        "MAC",
        "物理地址",
        "地址",
        "QoS",
        "标准",
        "长度",
    )
    return sum(1 for term in field_terms if term in text)


def _has_projection_fields(
    text: str,
    *,
    projection_field_count: int,
    has_filter_condition: bool,
) -> bool:
    if projection_field_count >= 2:
        return True
    if _contains_any(text, ("字段", "属性", "分别是什么", "分别是多少", "给出")):
        return True
    if projection_field_count >= 1 and _contains_any(text, ("值", "返回", "列出", "查出", "查询前", "查看前")):
        return not has_filter_condition
    return False


def _has_strong_structural_signal(features: QueryStructuralFeatures) -> bool:
    return any(
        (
            features.has_limit,
            features.has_order_signal,
            features.has_filter_condition,
            features.has_projection_fields,
            features.has_path_signal,
            features.has_relation_signal,
            features.has_group_signal,
            features.has_aggregation_signal,
            features.has_existence_signal,
            features.has_ratio_signal,
            features.has_set_signal,
            features.has_time_signal,
            features.has_comparison_signal,
        )
    )


def _term_count(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in text)


def _text_features(text: str) -> list[str]:
    if not text:
        return []
    features = list(text)
    for width in (2, 3):
        if len(text) >= width:
            features.extend(text[index : index + width] for index in range(0, len(text) - width + 1))
    return features


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(left_value * right_value for left_value, right_value in zip(left, right))


def _stable_hash(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("rule word lists must be YAML lists")
    return tuple(str(item) for item in value)


def _required_list(value: dict[str, object], key: str) -> list[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"missing required list: {key}")
    return item


def _required_str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"missing required string: {key}")
    return item
