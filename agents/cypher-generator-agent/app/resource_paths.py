from __future__ import annotations

from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = SERVICE_ROOT / "resources"

INTENT_RESOURCE_DIR = RESOURCE_ROOT / "intent"
SLOT_RESOURCE_DIR = RESOURCE_ROOT / "slots"
BUSINESS_RESOURCE_DIR = RESOURCE_ROOT / "business"
SEMANTIC_RESOURCE_DIR = RESOURCE_ROOT / "semantic"


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


def slot_lexicon_path() -> Path:
    return SLOT_RESOURCE_DIR / "lexicon.yaml"


def slot_value_aliases_path() -> Path:
    return SLOT_RESOURCE_DIR / "value_aliases.yaml"


def slot_parse_patterns_path() -> Path:
    return SLOT_RESOURCE_DIR / "parse_patterns.yaml"


def business_slot_schemas_path() -> Path:
    return BUSINESS_RESOURCE_DIR / "slot_schemas.yaml"


def semantic_layer_path() -> Path:
    return SEMANTIC_RESOURCE_DIR / "semantic_layer.yaml"
