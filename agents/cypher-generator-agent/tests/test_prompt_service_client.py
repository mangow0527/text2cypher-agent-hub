import asyncio
import unittest
from unittest.mock import patch

import httpx

from services.cypher_generator_agent.app.clients import KnowledgeAgentClient


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, json: dict[str, str]) -> httpx.Response:
        return self._response


class KnowledgeAgentClientTest(unittest.TestCase):
    def test_fetch_context_reads_knowledge_context_from_text_response(self) -> None:
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="Schema: (:Protocol)-[:HAS_TUNNEL]->(:Tunnel)",
            request=httpx.Request("POST", "http://knowledge-agent/api/knowledge/rag/prompt-package"),
        )
        client = KnowledgeAgentClient(base_url="http://knowledge-agent", timeout_seconds=5)

        with patch("services.cypher_generator_agent.app.clients.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            context = asyncio.run(client.fetch_context(id="qa-1", question="查询所有节点"))

        self.assertEqual(context, "Schema: (:Protocol)-[:HAS_TUNNEL]->(:Tunnel)")

    def test_fetch_context_accepts_json_prompt_response(self) -> None:
        response = httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json={"prompt": "Schema: (:Protocol)-[:HAS_TUNNEL]->(:Tunnel)"},
            request=httpx.Request("POST", "http://knowledge-agent/api/knowledge/rag/prompt-package"),
        )
        client = KnowledgeAgentClient(base_url="http://knowledge-agent", timeout_seconds=5)

        with patch("services.cypher_generator_agent.app.clients.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            context = asyncio.run(client.fetch_context(id="qa-1", question="查询所有节点"))

        self.assertEqual(context, "Schema: (:Protocol)-[:HAS_TUNNEL]->(:Tunnel)")


if __name__ == "__main__":
    unittest.main()
