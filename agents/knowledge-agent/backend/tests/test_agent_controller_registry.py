import unittest

from pydantic import BaseModel

from app.domain.agent.controller import LLMController
from app.domain.agent.models import AgentAction, AgentConstraints, AgentRun, RootCause
from app.domain.agent.tool_registry import AgentTool, ToolRegistry
from app.errors import AppError


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate_text(self, prompt_name: str, model_config: dict, **kwargs) -> str:
        return self.response


class EchoInput(BaseModel):
    value: str


class EchoTool(AgentTool):
    name = "echo"
    description = "Echo a string value for testing."
    input_model = EchoInput

    def execute(self, arguments: EchoInput) -> dict:
        return {"value": arguments.value}


class SideEffectTool(AgentTool):
    name = "side_effect"
    description = "Dangerous side-effect tool used for allowlist tests."
    input_model = EchoInput
    side_effect = True

    def execute(self, arguments: EchoInput) -> dict:
        return {"value": arguments.value}


class ControllerRegistryTest(unittest.TestCase):
    def test_controller_parses_tool_call_json(self) -> None:
        controller = LLMController(
            FakeGateway('{"action":"tool_call","tool_name":"echo","arguments":{"value":"ok"},"reason_summary":"test"}')
        )
        action = controller.decide_next_action(
            context={"qa_id": "qa_001"},
            memory=[],
            tools=[
                {
                    "name": "echo",
                    "description": "Echo a string value for testing.",
                    "input_schema": EchoInput.model_json_schema(),
                }
            ],
        )
        self.assertEqual(action.tool_name, "echo")

    def test_controller_rejects_free_text(self) -> None:
        controller = LLMController(FakeGateway("我想查知识"))
        with self.assertRaises(AppError):
            controller.decide_next_action(context={}, memory=[], tools=[])

    def test_registry_exposes_tool_specs_with_argument_schema(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        run = AgentRun(
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="x", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(allowed_tools=["echo"]),
        )

        specs = registry.allowed_tool_specs(run)

        self.assertEqual(specs[0]["name"], "echo")
        self.assertIn("value", specs[0]["input_schema"]["properties"])

    def test_default_allowlist_does_not_expose_side_effect_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(SideEffectTool())
        run = AgentRun(
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="x", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(allowed_tools=["echo", "side_effect"]),
        )

        self.assertEqual(registry.allowed_tool_names(run), ["echo"])

    def test_registry_executes_only_allowed_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        run = AgentRun(
            qa_id="qa_001",
            goal="repair",
            root_cause=RootCause(type="x", summary="s", suggested_fix="f"),
            constraints=AgentConstraints(allowed_tools=["echo"]),
        )
        observation = registry.execute(
            run,
            AgentAction(action="tool_call", tool_name="echo", arguments={"value": "ok"}, reason_summary="test"),
        )
        self.assertEqual(observation["value"], "ok")

        blocked_run = run.model_copy(update={"constraints": AgentConstraints(allowed_tools=["other"])})
        with self.assertRaises(AppError):
            registry.execute(
                blocked_run,
                AgentAction(action="tool_call", tool_name="echo", arguments={"value": "ok"}, reason_summary="test"),
            )


if __name__ == "__main__":
    unittest.main()
