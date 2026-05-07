from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from app.domain.models import JobRecord, JobRequest, JobStatus, StageRecord
from app.orchestrator.service import Orchestrator
from app.storage.artifact_store import ArtifactStore
from app.storage.job_log_store import JobLogStore
from app.storage.job_store import JobStore


class BusinessStageTests(unittest.TestCase):
    def test_job_log_store_writes_job_scoped_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobLogStore(Path(tmp))
            store.append("job_123", "generate_cypher", "info", "started")
            path = Path(tmp) / "jobs" / "job_123.log"
            self.assertTrue(path.exists())
            self.assertIn("generate_cypher", path.read_text(encoding="utf-8"))

    def test_business_stage_summary_contains_seven_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = Orchestrator(
                job_store=JobStore(root=root / "job-reports"),
                artifact_store=ArtifactStore(root=root / "artifacts"),
            )
            job = orchestrator.create_job(JobRequest(schema_input={"schema": "x"}))
            job.status = JobStatus.PACKAGED
            job.metrics["dispatch"] = {"status": "success", "total": 1, "success": 1, "failed": 0, "message": "sent"}
            job.stages = [
                StageRecord(to_status=JobStatus.SCHEMA_READY, summary="Resolved schema", duration_ms=10),
                StageRecord(to_status=JobStatus.SKELETON_READY, summary="Built skeletons", duration_ms=11),
                StageRecord(to_status=JobStatus.CYPHER_READY, summary="Instantiated candidates", duration_ms=12),
                StageRecord(to_status=JobStatus.VALIDATED, summary="Validated candidates", duration_ms=13),
                StageRecord(to_status=JobStatus.QUESTIONS_READY, summary="Generated QA samples", duration_ms=14),
                StageRecord(to_status=JobStatus.ROUNDTRIP_DONE, summary="Completed roundtrip checks", duration_ms=15),
                StageRecord(to_status=JobStatus.DEDUPED, summary="Deduplicated and split samples", duration_ms=16),
                StageRecord(to_status=JobStatus.PACKAGED, summary="Packaged artifacts", duration_ms=17),
            ]

            stages = orchestrator._build_business_stage_summary(job)
            report = orchestrator.report_builder.build([], stages=job.stages, dispatch=job.metrics["dispatch"])

            self.assertEqual([item["key"] for item in stages], [
                "ground_schema",
                "spec_coverage",
                "generate_cypher",
                "tugraph_validate",
                "generate_qa",
                "roundtrip_check",
                "release_dispatch",
            ])
            self.assertEqual(len(stages), 7)
            self.assertEqual(report["business_stages"], stages)

    def test_run_stage_persists_live_business_stage_snapshot_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = Orchestrator(
                job_store=JobStore(root=root / "job-reports"),
                artifact_store=ArtifactStore(root=root / "artifacts"),
            )
            job = orchestrator.create_job(JobRequest(schema_input={"schema": "x"}))

            persisted_snapshots = []

            def stage_body():
                persisted = orchestrator.job_store.get(job.job_id)
                persisted_snapshots.append(persisted.metrics["business_stages"])
                return {"ok": True}

            orchestrator._run_stage(job, JobStatus.CYPHER_READY, "Instantiated candidates", stage_body)

            self.assertTrue(persisted_snapshots)
            running_stage = next(item for item in persisted_snapshots[0] if item["key"] == "generate_cypher")
            self.assertEqual(running_stage["status"], "running")
            self.assertEqual(running_stage["message"], "Instantiated candidates")
            self.assertIsNotNone(running_stage["duration_ms"])

    def test_packaged_internal_status_maps_to_release_dispatch_logical_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = Orchestrator(
                job_store=JobStore(root=root / "job-reports"),
                artifact_store=ArtifactStore(root=root / "artifacts"),
            )

            self.assertEqual(orchestrator._internal_to_business_stage(JobStatus.PACKAGED), "release_dispatch")

    def test_live_business_stage_summary_keeps_future_steps_pending(self):
        stages = [
            StageRecord(to_status=JobStatus.CYPHER_READY, summary="Instantiated candidates"),
        ]

        summary = Orchestrator()._build_business_stage_summary(
            JobRecord(request=JobRequest(schema_input={"schema": "x"}), stages=stages)
        )

        by_key = {item["key"]: item for item in summary}
        self.assertEqual(by_key["ground_schema"]["status"], "skipped")
        self.assertEqual(by_key["spec_coverage"]["status"], "skipped")
        self.assertEqual(by_key["generate_cypher"]["status"], "running")
        self.assertEqual(by_key["tugraph_validate"]["status"], "pending")
        self.assertEqual(by_key["generate_qa"]["status"], "pending")
        self.assertEqual(by_key["roundtrip_check"]["status"], "pending")
        self.assertEqual(by_key["release_dispatch"]["status"], "pending")

    def test_job_snapshot_recomputes_live_duration_for_running_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = Orchestrator(
                job_store=JobStore(root=root / "job-reports"),
                artifact_store=ArtifactStore(root=root / "artifacts"),
            )
            job = orchestrator.create_job(JobRequest(schema_input={"schema": "x"}))
            job.status = JobStatus.CYPHER_READY
            job.stages.append(
                StageRecord(
                    to_status=JobStatus.CYPHER_READY,
                    summary="Instantiated candidates",
                    started_at=(datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
                )
            )
            orchestrator.job_store.save(job)

            snapshot = orchestrator.get_job_snapshot(job.job_id)
            running_stage = next(item for item in snapshot.metrics["business_stages"] if item["key"] == "generate_cypher")

            self.assertEqual(running_stage["status"], "running")
            self.assertGreaterEqual(running_stage["duration_ms"], 1500)

    def test_query_plan_target_budget_scales_with_requested_count(self):
        orchestrator = Orchestrator()

        self.assertEqual(orchestrator._query_plan_target_count(1, 8), 8)
        self.assertEqual(orchestrator._query_plan_target_count(1, 16), 8)
        self.assertEqual(orchestrator._query_plan_target_count(10, 18), 18)
        self.assertEqual(orchestrator._query_plan_target_count(20, 64), 28)

    def test_online_effective_limits_keeps_initial_cypher_buffer(self):
        orchestrator = Orchestrator()
        request = JobRequest(mode="online", schema_input={"schema": "x"}, output_config={"target_qa_count": 10})

        limits = orchestrator._effective_limits(request)

        self.assertGreater(limits.max_skeletons, request.output_config.target_qa_count)

    def test_effective_limits_reduce_variant_and_candidate_budgets_for_large_batch(self):
        orchestrator = Orchestrator()
        request = JobRequest(mode="offline", schema_input={"schema": "x"}, output_config={"target_qa_count": 20})

        limits = orchestrator._effective_limits(request)

        self.assertEqual(limits.max_candidates_per_skeleton, 2)
        self.assertEqual(limits.max_variants_per_question, 1)

    def test_large_batch_disables_llm_cypher_enrichment(self):
        orchestrator = Orchestrator()

        self.assertFalse(orchestrator._should_enable_llm_cypher_enrichment(20, "offline"))
        self.assertTrue(orchestrator._should_enable_llm_cypher_enrichment(5, "offline"))
        self.assertTrue(orchestrator._should_enable_llm_cypher_enrichment(1, "online"))
