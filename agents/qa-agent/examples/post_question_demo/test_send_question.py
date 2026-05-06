import json
import unittest
from unittest.mock import patch

from send_question import API_URL, submit_question


class _FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SubmitQuestionTests(unittest.TestCase):
    @patch("send_question.urllib.request.build_opener")
    def test_submit_question_posts_expected_json(self, mock_build_opener):
        opener = mock_build_opener.return_value
        opener.open.return_value = _FakeResponse('{"ok": true}', status=200)

        result = submit_question(question_id="xxx", question="今天天气如何")

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"], '{"ok": true}')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, API_URL)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"id": "xxx", "question": "今天天气如何"},
        )


if __name__ == "__main__":
    unittest.main()
