from __future__ import annotations

from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = SERVICE_ROOT / "resources"

INTENT_RESOURCE_DIR = RESOURCE_ROOT / "intent"
SEMANTIC_VIEW_RESOURCE_DIR = RESOURCE_ROOT / "semantic_views"


def intent_taxonomy_path() -> Path:
    return INTENT_RESOURCE_DIR / "taxonomy.yaml"


def intent_rules_path() -> Path:
    return INTENT_RESOURCE_DIR / "rules.yaml"


def intent_embedding_corpus_path() -> Path:
    return INTENT_RESOURCE_DIR / "embedding_corpus.jsonl"


def intent_embedding_index_path() -> Path:
    return INTENT_RESOURCE_DIR / "embedding_index.jsonl"


def intent_eval_set_path() -> Path:
    return INTENT_RESOURCE_DIR / "eval_set.jsonl"


def intent_llm_fewshots_path() -> Path:
    return INTENT_RESOURCE_DIR / "llm_fewshots.yaml"


def graph_semantic_view_path() -> Path:
    return SEMANTIC_VIEW_RESOURCE_DIR / "network_graph_semantic_view.yaml"
