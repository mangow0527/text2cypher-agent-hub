from __future__ import annotations

from services.cypher_generator_agent.app import resource_paths


def test_default_resources_are_grouped_by_pipeline_stage() -> None:
    root = resource_paths.RESOURCE_ROOT

    expected_paths = [
        resource_paths.intent_taxonomy_path(),
        resource_paths.intent_rules_path(),
        resource_paths.intent_embedding_corpus_path(),
        resource_paths.intent_eval_set_path(),
        resource_paths.intent_llm_fewshots_path(),
        resource_paths.graph_semantic_view_path(),
    ]

    assert root.name == "resources"
    assert {path.parent.relative_to(root).as_posix() for path in expected_paths} == {
        "intent",
        "semantic_views",
    }
    assert all(path.exists() for path in expected_paths)
    assert not (root.parent / "config" / "slot_dictionary.yaml").exists()
    assert not (root / "slots").exists()
    assert not (root / "business").exists()
    assert not (root / "semantic" / "semantic_layer.yaml").exists()
