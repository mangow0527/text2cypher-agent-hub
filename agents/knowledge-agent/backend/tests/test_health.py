import unittest

from fastapi.testclient import TestClient

from app.entrypoints.api.main import app


class HealthApiTest(unittest.TestCase):
    def test_health_returns_ok(self) -> None:
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
