from __future__ import annotations

from pathlib import Path

import yaml

from services.cypher_generator_agent.app.question_preprocessing.text_cleaning import clean_text


def test_text_cleaning_config_uses_boundary_phrases_not_correction_markers() -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "question_preprocessing"
        / "text_cleaning.yaml"
    )

    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    assert "correction_markers" not in config
    before_phrases = config["punctuation_policy"]["light_punctuation_insertion"]["before_phrases"]
    assert before_phrases["enabled"] is True
    assert "哦不对" in before_phrases["items"]


def test_text_cleaning_inserts_light_punctuation_before_boundary_phrase() -> None:
    result = clean_text(
        "查询一下金牌服务 哦不对是银牌服务",
        config={
            "whitespace_policy": {
                "collapse_whitespace": {"enabled": True},
                "remove_safe_chinese_inner_spaces": {"enabled": True},
            },
            "punctuation_policy": {
                "collapse_rules": [],
                "light_punctuation_insertion": {
                    "before_phrases": {
                        "enabled": True,
                        "punctuation": "，",
                        "items": ["哦不对"],
                    }
                },
            },
            "split_word_repairs": {"items": []},
        },
    )

    assert result.cleaned_question == "查询一下金牌服务，哦不对是银牌服务"
    assert [normalization.rule for normalization in result.normalizations] == [
        "insert_light_punctuation_before_boundary_phrase"
    ]
