from __future__ import annotations

import unittest

from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.models import (
    CanonicalSchemaSpec,
    GenerationLimits,
    JobRequest,
    ResultSignature,
    RuntimeMeta,
    TuGraphConfig,
)
from app.domain.coverage.service import CoverageService
from app.domain.schema.value_catalog import ValueCatalogProfiler
from app.orchestrator.service import Orchestrator
from app.storage.artifact_store import ArtifactStore
from app.storage.job_store import JobStore


class FakeGraphExecutor:
    def __init__(self) -> None:
        self.cyphers: list[str] = []

    def execute(self, cypher: str, config: TuGraphConfig):
        self.cyphers.append(cypher)
        if "n.quality_of_service" in cypher:
            return (
                RuntimeMeta(latency_ms=1, planner="fake-graph"),
                ResultSignature(
                    columns=["value"],
                    row_count=2,
                    result_preview=[{"value": "Gold"}, {"value": "Bronze"}],
                    result_rows=[{"value": "Gold"}, {"value": "Bronze"}],
                ),
                True,
            )
        if "n.bandwidth" in cypher:
            return (
                RuntimeMeta(latency_ms=1, planner="fake-graph"),
                ResultSignature(
                    columns=["value"],
                    row_count=1,
                    result_preview=[{"value": 120.0}],
                    result_rows=[{"value": 120.0}],
                ),
                True,
            )
        return RuntimeMeta(latency_ms=1, planner="fake-graph"), ResultSignature(), True


class ValueCatalogProfilerTests(unittest.TestCase):
    def test_enrich_samples_real_values_for_missing_catalog_entries(self) -> None:
        schema = CanonicalSchemaSpec(
            node_types=["Service"],
            node_properties={"Service": {"quality_of_service": "STRING", "bandwidth": "DOUBLE"}},
            value_catalog={},
        )

        enriched = ValueCatalogProfiler(graph_executor=FakeGraphExecutor()).enrich(
            schema,
            TuGraphConfig(base_url="http://tugraph", username="admin", password="pw", graph="network"),
        )

        self.assertEqual(enriched.value_catalog["Service.quality_of_service"], ["Gold", "Bronze"])
        self.assertEqual(enriched.value_catalog["Service.bandwidth"], [120.0])
        self.assertEqual(schema.value_catalog, {})

    def test_coverage_uses_profiled_values_instead_of_fallback_literals(self) -> None:
        schema = CanonicalSchemaSpec(
            node_types=["Service"],
            node_properties={"Service": {"quality_of_service": "STRING"}},
            value_catalog={"Service.quality_of_service": ["Gold"]},
        )

        specs = CoverageService().build_specs(
            schema,
            GenerationLimits(max_skeletons=4, max_candidates_per_skeleton=1, max_variants_per_question=1),
            target_qa_count=1,
            difficulty_targets={"L2": 1},
            diversity_key="l2-profiled-value",
        )

        self.assertEqual(specs[0].bindings["value"], "Gold")

    def test_orchestrator_enriches_schema_before_building_l2_coverage(self) -> None:
        schema = CanonicalSchemaSpec(
            node_types=["Service"],
            node_properties={"Service": {"quality_of_service": "STRING"}},
        )
        profiler = RecordingProfiler({"Service.quality_of_service": ["Gold"]})
        coverage = RecordingCoverageService()

        with TemporaryDirectory() as tmpdir:
            orchestrator = Orchestrator(
                job_store=JobStore(root=Path(tmpdir) / "jobs"),
                artifact_store=ArtifactStore(root=Path(tmpdir) / "artifacts"),
                schema_service=FakeSchemaService(schema),
                source_resolver=FakeSourceResolver(),
                schema_compatibility_service=FakeCompatibilityService(),
                value_catalog_profiler=profiler,
                coverage_service=coverage,
                generation_service=NoopGenerationService(),
                question_service=NoopQuestionService(),
                roundtrip_service=NoopRoundtripService(),
                qa_dispatcher=NoopDispatcher(),
            )
            job = orchestrator.create_job(
                JobRequest(
                    output_config={"difficulty_targets": {"L2": 1}},
                    validation_config={"roundtrip_required": False},
                )
            )

            updated = orchestrator.run_job(job.job_id)

        self.assertTrue(profiler.called)
        self.assertEqual(coverage.first_value, "Gold")
        self.assertEqual(updated.status.value, "failed")


class RecordingProfiler:
    def __init__(self, values: dict[str, list[str]]) -> None:
        self.values = values
        self.called = False

    def enrich(self, schema: CanonicalSchemaSpec, config: TuGraphConfig) -> CanonicalSchemaSpec:
        self.called = True
        enriched = schema.model_copy(deep=True)
        enriched.value_catalog.update(self.values)
        return enriched


class RecordingCoverageService(CoverageService):
    def __init__(self) -> None:
        super().__init__()
        self.first_value = None

    def build_specs(self, schema, limits, target_qa_count, difficulty_targets=None, diversity_key=None):
        specs = super().build_specs(schema, limits, target_qa_count, difficulty_targets, diversity_key)
        if specs and self.first_value is None:
            self.first_value = specs[0].bindings.get("value")
        return specs


class FakeSchemaService:
    def __init__(self, schema: CanonicalSchemaSpec) -> None:
        self.schema = schema

    def normalize(self, schema_input):
        return self.schema


class FakeSourceResolver:
    def resolve_schema(self, source, fallback_input=None):
        return {}

    def resolve_tugraph(self, source, config):
        return TuGraphConfig(base_url="http://tugraph", username="admin", password="pw", graph="network")


class FakeCompatibilityService:
    def assert_compatible(self, schema, config):
        return {"ok": True}


class NoopGenerationService:
    def instantiate_candidates_from_specs(self, schema, coverage_specs, limits, model_config=None):
        return []


class NoopQuestionService:
    def generate_batch(self, validated, schema, llm_config, max_variants):
        return []


class NoopRoundtripService:
    def check(self, sample, model_config):
        return True, sample.question_variants_zh, sample.question_variant_styles


class NoopDispatcher:
    def dispatch_samples(self, samples):
        return {"enabled": False, "status": "skipped", "total": len(samples)}


if __name__ == "__main__":
    unittest.main()
