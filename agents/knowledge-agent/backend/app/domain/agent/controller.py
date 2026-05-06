from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.domain.agent.models import AgentAction
from app.errors import AppError


class LLMController:
    def __init__(self, model_gateway, model_config: dict[str, Any] | None = None) -> None:
        self.model_gateway = model_gateway
        self.model_config = model_config or {"model": "glm-5", "temperature": 0.1, "max_output_tokens": 800}

    def decide_next_action(self, context: dict[str, Any], memory: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentAction:
        raw = self.model_gateway.generate_text(
            "agent_next_action",
            self.model_config,
            prompt=self._build_prompt(context=context, memory=memory, tools=tools),
        )
        try:
            return AgentAction.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise AppError("AGENT_CONTROLLER_INVALID_ACTION", f"Agent controller returned invalid action: {raw}") from exc

    def _build_prompt(self, context: dict[str, Any], memory: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
        return (
            "你是 Guarded Knowledge Repair Agent 的控制器。"
            "你必须只输出合法 JSON，不能输出解释文本。"
            "你可以选择 tool_call、request_human_review 或 final。"
            "final 不能声明 completed，只能声明 ready_for_review 或 rejected。"
            f"\n可用工具：{json.dumps(tools, ensure_ascii=False)}"
            f"\n当前状态：{json.dumps(context, ensure_ascii=False)}"
            f"\n相关记忆：{json.dumps(memory, ensure_ascii=False)}"
        )
