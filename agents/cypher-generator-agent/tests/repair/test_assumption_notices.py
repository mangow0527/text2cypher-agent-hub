from __future__ import annotations

from services.cypher_generator_agent.app.repair.controller import RepairController
from services.cypher_generator_agent.app.repair.notices import render_user_visible_notices


def test_high_confidence_fuzzy_literal_continues_with_template_derived_chinese_notice() -> None:
    assumption = {
        "kind": "literal_binding",
        "raw": "防火墙",
        "assumed_as": "firewall",
        "confidence": 0.87,
        "property": "NetworkElement.elem_type",
    }

    decision = RepairController().decide(
        {
            "schema_version": "repair_controller_input_v1",
            "trace_id": "q-20260527-002",
            "question": "防火墙设备有哪些",
            "attempt_no": 1,
            "selected_bindings": {},
            "normalized_dsl": None,
            "validator_errors": [
                {
                    "code": "high_confidence_fuzzy_literal",
                    "message": "literal accepted with assumption",
                    "severity": "warning",
                    "repairable": False,
                    "details": {"assumption": assumption},
                }
            ],
            "cypher_validation_errors": [],
            "history": [],
            "assumptions": [],
        }
    )

    assert decision.decision == "continue_with_assumption"
    assert decision.reason_code == "high_confidence_fuzzy_literal"
    assert [item.model_dump(exclude_none=True) for item in decision.assumptions] == [assumption]
    assert decision.derived_user_visible_notices == [
        "我把“防火墙”理解为设备类型 firewall。"
    ]
    assert decision.derived_user_visible_notices == render_user_visible_notices(decision.assumptions)


def test_free_text_assumption_message_is_not_rendered_as_user_notice() -> None:
    notices = render_user_visible_notices(
        [
            {
                "kind": "unknown_assumption",
                "raw": "foo",
                "assumed_as": "bar",
                "message": "do not pass through reviewer text",
            }
        ]
    )

    assert notices == []


def test_binder_style_literal_assumption_renders_with_same_template() -> None:
    notices = render_user_visible_notices(
        [
            {
                "type": "literal_binding",
                "raw_literal": "防火墙",
                "value": "firewall",
                "confidence": 0.86,
                "property": "NetworkElement.elem_type",
            }
        ]
    )

    assert notices == ["我把“防火墙”理解为设备类型 firewall。"]
