from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage.release_history_store import ReleaseHistoryStore


class ReleaseHistoryStoreTests(unittest.TestCase):
    def test_load_signatures_ignores_hidden_and_invalid_history_files(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "job_valid.jsonl").write_text(
                json.dumps({"question": "网络中有哪些设备？", "cypher": "MATCH (n) RETURN n"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (root / "._job_invalid.jsonl").write_bytes(b"\xa3\x10not-utf8")
            (root / "job_broken.jsonl").write_text("{not json}\n", encoding="utf-8")

            signatures = ReleaseHistoryStore(root=root).load_signatures()

        self.assertEqual(signatures["questions"], {"网络中有哪些设备？"})
        self.assertEqual(signatures["cyphers"], {"match (n) return n"})


if __name__ == "__main__":
    unittest.main()
