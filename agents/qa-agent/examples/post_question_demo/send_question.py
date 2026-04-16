"""Send a POST request to the QA questions API with Python stdlib only."""

import argparse
import json
import urllib.request


API_URL = "http://172.20.10.14:8000/api/v1/qa/questions"


def submit_question(
    question_id: str = "qa_101b3ee246a7",
    question: str = "总共有多少节点",
    url: str = API_URL,
    timeout: int = 10,
) -> dict:
    payload = {"id": question_id, "question": question}
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return {"status": response.status, "body": body}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="POST a question payload to the QA questions API."
    )
    parser.add_argument("--id", default="xxx", help="Question ID to send.")
    parser.add_argument(
        "--question",
        default="今天天气如何",
        help="Question text to send.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Request timeout in seconds.",
    )
    args = parser.parse_args()

    result = submit_question(
        question_id=args.id,
        question=args.question,
        timeout=args.timeout,
    )
    print(f"status: {result['status']}")
    print(result["body"])


if __name__ == "__main__":
    main()
