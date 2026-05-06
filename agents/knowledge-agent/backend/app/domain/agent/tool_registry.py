from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ValidationError

from app.domain.agent.models import AgentAction, AgentRun
from app.errors import AppError


class AgentTool:
    name: ClassVar[str]
    description: ClassVar[str] = ""
    input_model: ClassVar[type[BaseModel]]
    side_effect: ClassVar[bool] = False

    def execute(self, arguments: BaseModel) -> dict:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def allowed_tool_names(self, run: AgentRun) -> list[str]:
        configured = run.constraints.allowed_tools
        return [name for name in configured if name in self._tools and not self._tools[name].side_effect]

    def allowed_tool_specs(self, run: AgentRun) -> list[dict]:
        return [
            {
                "name": name,
                "description": self._tools[name].description,
                "input_schema": self._tools[name].input_model.model_json_schema(),
            }
            for name in self.allowed_tool_names(run)
        ]

    def execute(self, run: AgentRun, action: AgentAction) -> dict:
        if action.action != "tool_call" or not action.tool_name:
            raise AppError("AGENT_ACTION_NOT_TOOL_CALL", "Only tool_call actions can be executed.")
        if action.tool_name not in self._tools:
            raise AppError("AGENT_TOOL_NOT_FOUND", f"Agent tool not found: {action.tool_name}")
        if run.constraints.allowed_tools and action.tool_name not in run.constraints.allowed_tools:
            raise AppError("AGENT_TOOL_NOT_ALLOWED", f"Agent tool is not allowed for this run: {action.tool_name}")
        tool = self._tools[action.tool_name]
        if tool.side_effect:
            raise AppError("AGENT_SIDE_EFFECT_TOOL_BLOCKED", f"Side-effect tool cannot be called by controller: {action.tool_name}")
        try:
            parsed = tool.input_model.model_validate(action.arguments)
        except ValidationError as exc:
            raise AppError("AGENT_TOOL_ARGUMENT_INVALID", str(exc)) from exc
        return tool.execute(parsed)
