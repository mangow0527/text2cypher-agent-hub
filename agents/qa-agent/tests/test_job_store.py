from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage.job_store import JobStore


class JobStoreTests(unittest.TestCase):
    def test_list_ignores_hidden_and_invalid_job_files(self) -> None:
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "job_valid.json").write_text(
                '{"job_id":"job_valid","status":"created","created_at":"2026-04-14T00:00:00+00:00","updated_at":"2026-04-14T00:00:00+00:00","request":{"mode":"online","schema_input":null,"schema_source":{"type":"inline","inline_json":null,"file_path":null,"url":null,"method":"GET","body":null,"headers":{}},"taxonomy_version":"v1","generation_limits":{"max_skeletons":64,"max_candidates_per_skeleton":4,"max_variants_per_question":5},"validation_config":{"require_runtime_validation":true,"allow_empty_results":true,"roundtrip_required":true},"llm_config":{"model":"glm-5","temperature":0.2,"max_output_tokens":1200},"tugraph_source":{"type":"env"},"tugraph_config":{"base_url":null,"username":null,"password":null,"graph":null,"cypher_endpoint":"/cypher","timeout_seconds":30},"output_config":{"split_seed_limit":10,"split_gold_limit":20,"target_qa_count":10}},"metrics":{},"stages":[],"artifacts":{},"errors":[]}',
                encoding="utf-8",
            )
            (root / "._job_invalid.json").write_bytes(b"\xa3\x10not-utf8")
            (root / "job_broken.json").write_text("{not json}", encoding="utf-8")

            records = list(JobStore(root=root).list())

        self.assertEqual([record.job_id for record in records], ["job_valid"])


if __name__ == "__main__":
    unittest.main()
