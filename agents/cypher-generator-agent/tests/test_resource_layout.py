from __future__ import annotations

import yaml

from services.cypher_generator_agent.app import resource_paths
from services.cypher_generator_agent.app.slot_matching import load_default_slot_dictionary


def test_default_resources_are_grouped_by_pipeline_stage() -> None:
    root = resource_paths.RESOURCE_ROOT

    expected_paths = [
        resource_paths.intent_taxonomy_path(),
        resource_paths.intent_rules_path(),
        resource_paths.intent_embedding_corpus_path(),
        resource_paths.intent_eval_set_path(),
        resource_paths.intent_llm_fewshots_path(),
        resource_paths.slot_lexicon_path(),
        resource_paths.slot_value_aliases_path(),
        resource_paths.slot_parse_patterns_path(),
        resource_paths.business_slot_schemas_path(),
        resource_paths.semantic_layer_path(),
    ]

    assert root.name == "resources"
    assert {path.parent.relative_to(root).as_posix() for path in expected_paths} == {
        "business",
        "intent",
        "semantic",
        "slots",
    }
    assert all(path.exists() for path in expected_paths)
    assert not (root.parent / "config" / "slot_dictionary.yaml").exists()


def test_slot_resources_have_single_responsibility_sections() -> None:
    lexicon = yaml.safe_load(resource_paths.slot_lexicon_path().read_text(encoding="utf-8"))
    aliases = yaml.safe_load(resource_paths.slot_value_aliases_path().read_text(encoding="utf-8"))
    patterns = yaml.safe_load(resource_paths.slot_parse_patterns_path().read_text(encoding="utf-8"))

    assert set(lexicon) == {"entities", "relationships", "properties", "metric_templates"}
    assert set(aliases) == {"values"}
    assert set(patterns) == {"default_filter_entity", "entity_properties", "order", "group_by", "limit"}

    dictionary = load_default_slot_dictionary()
    assert "service" in dictionary["entities"]
    assert "quality_of_service" in dictionary["values"]
    assert dictionary["default_filter_entity"]["quality_of_service"] == "service"
