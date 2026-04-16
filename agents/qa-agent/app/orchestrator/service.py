from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.domain.generation.service import GenerationService
from app.domain.models import JobRecord, JobRequest, JobStatus, QASample, StageRecord
from app.domain.questioning.service import QuestionService, normalize_cypher, normalize_question
from app.domain.roundtrip.service import RoundtripService
from app.domain.schema.compatibility_service import SchemaCompatibilityService
from app.domain.schema.service import SchemaService
from app.domain.schema.source_resolver import SourceResolver
from app.domain.validation.service import ValidationService
from app.integrations.qa_dispatcher import QADispatcher
from app.reports.builder import ReportBuilder
from app.storage.artifact_store import ArtifactStore
from app.storage.job_store import JobStore
from app.storage.release_history_store import ReleaseHistoryStore


class NoValidQAGeneratedError(RuntimeError):
    code = "NO_VALID_QA_GENERATED"


class Orchestrator:
    def __init__(
        self,
        job_store: JobStore | None = None,
        artifact_store: ArtifactStore | None = None,
        schema_service: SchemaService | None = None,
        source_resolver: SourceResolver | None = None,
        schema_compatibility_service: SchemaCompatibilityService | None = None,
        generation_service: GenerationService | None = None,
        validation_service: ValidationService | None = None,
        question_service: QuestionService | None = None,
        roundtrip_service: RoundtripService | None = None,
        report_builder: ReportBuilder | None = None,
        qa_dispatcher: QADispatcher | None = None,
        release_history_store: ReleaseHistoryStore | None = None,
    ) -> None:
        self.job_store = job_store or JobStore()
        self.artifact_store = artifact_store or ArtifactStore()
        self.schema_service = schema_service or SchemaService()
        self.source_resolver = source_resolver or SourceResolver()
        self.schema_compatibility_service = schema_compatibility_service or SchemaCompatibilityService()
        self.generation_service = generation_service or GenerationService()
        self.validation_service = validation_service or ValidationService()
        self.question_service = question_service or QuestionService()
        self.roundtrip_service = roundtrip_service or RoundtripService()
        self.report_builder = report_builder or ReportBuilder()
        self.qa_dispatcher = qa_dispatcher or QADispatcher()
        self.release_history_store = release_history_store or ReleaseHistoryStore(
            root=(artifact_store.root / "releases") if artifact_store else None
        )

    def create_job(self, request: JobRequest) -> JobRecord:
        job = JobRecord(request=request)
        self.job_store.save(job)
        return job

    def create_and_run_job(self, request: JobRequest) -> JobRecord:
        job = self.create_job(request)
        return self.run_job(job.job_id)

    def delete_job(self, job_id: str) -> None:
        job = self.job_store.get(job_id)
        self.artifact_store.delete_paths(job.artifacts.values())
        self.job_store.delete(job_id)

    def redispatch_job(self, job_id: str) -> JobRecord:
        job = self.job_store.get(job_id)
        releases_path = job.artifacts.get("releases")
        if not releases_path:
            raise FileNotFoundError(f"Job {job_id} does not have a releases artifact.")
        rows = [
            json.loads(line)
            for line in Path(releases_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        dispatch_result = self.qa_dispatcher.dispatch_release_rows(rows)
        history = list(job.metrics.get("dispatch_history", []))
        history.append(
            {
                "dispatch_id": f"disp_{uuid4().hex[:12]}",
                "trigger": "manual",
                "created_at": self._now(),
                **dispatch_result,
            }
        )
        job.metrics["dispatch"] = dispatch_result
        job.metrics["dispatch_history"] = history
        job.updated_at = self._now()
        self.job_store.save(job)
        return job

    def run_job(self, job_id: str) -> JobRecord:
        job = self.job_store.get(job_id)
        paths = self.artifact_store.ensure_job_dirs(job_id)
        limits = self._effective_limits(job.request)
        llm_config = self._effective_llm_config(job.request)

        try:
            resolved_schema_input = self._run_stage(
                job,
                JobStatus.SCHEMA_READY,
                "Resolved and normalized schema",
                lambda: self.schema_service.normalize(
                    self.source_resolver.resolve_schema(job.request.schema_source, job.request.schema_input)
                ),
            )
            schema = resolved_schema_input
            self.artifact_store.write_json(paths["schema"], schema.model_dump())
            job.artifacts["schema"] = str(paths["schema"])
            resolved_tugraph = self.source_resolver.resolve_tugraph(job.request.tugraph_source, job.request.tugraph_config)
            self.schema_compatibility_service.assert_compatible(schema, resolved_tugraph)

            attempt_count = 3
            aggregated_skeletons = []
            aggregated_candidates = []
            aggregated_validated = []
            aggregated_qa = []
            aggregated_roundtrip = []
            selection_meta = {}
            deduped = []
            for attempt in range(1, attempt_count + 1):
                diversity_key = f"{job.job_id}:attempt:{attempt}"
                skeletons = self._run_stage(
                    job,
                    JobStatus.SKELETON_READY,
                    f"Built skeletons (attempt {attempt}/{attempt_count})",
                    lambda attempt_key=diversity_key: self.generation_service.build_skeletons(
                        schema,
                        limits,
                        diversity_key=attempt_key,
                    ),
                )
                aggregated_skeletons.extend(skeletons)

                candidates = self._run_stage(
                    job,
                    JobStatus.CYPHER_READY,
                    f"Instantiated candidates (attempt {attempt}/{attempt_count})",
                    lambda current_skeletons=skeletons: self._dedupe_candidates(
                        self.generation_service.instantiate_candidates(schema, current_skeletons, limits, llm_config)
                    ),
                )
                aggregated_candidates.extend(candidates)
                aggregated_candidates = self._dedupe_candidates(aggregated_candidates)

                validated = self._run_stage(
                    job,
                    JobStatus.VALIDATED,
                    f"Validated candidates (attempt {attempt}/{attempt_count})",
                    lambda current_candidates=candidates: [
                        self.validation_service.validate(
                            candidate,
                            schema,
                            job.request.validation_config,
                            resolved_tugraph,
                        )
                        for candidate in current_candidates
                    ],
                )
                validated = [
                    item
                    for item in validated
                    if all(
                        [
                            item.validation.syntax,
                            item.validation.schema_valid,
                            item.validation.type_value,
                            item.validation.query_type_valid,
                            item.validation.family_valid,
                            item.validation.difficulty_valid,
                            item.validation.runtime,
                            item.validation.result_sanity,
                        ]
                    )
                ]
                aggregated_validated.extend(validated)
                aggregated_validated = self._dedupe_validated(aggregated_validated)

                qa_samples = self._run_stage(
                    job,
                    JobStatus.QUESTIONS_READY,
                    f"Generated QA samples (attempt {attempt}/{attempt_count})",
                    lambda current_validated=list(aggregated_validated): self._select_best_by_question(
                        self._generate_questions(
                            current_validated,
                            schema,
                            llm_config,
                            limits.max_variants_per_question,
                            job.request.mode.value,
                        )
                    ),
                )
                aggregated_qa = qa_samples

                roundtrip = self._run_stage(
                    job,
                    JobStatus.ROUNDTRIP_DONE,
                    f"Completed roundtrip checks (attempt {attempt}/{attempt_count})",
                    lambda current_qa=list(aggregated_qa): self._apply_roundtrip(
                        job,
                        current_qa,
                        llm_config,
                        job.request.mode.value,
                    ),
                )
                aggregated_roundtrip = self._select_best_by_question(roundtrip)

                deduped, selection_meta = self._run_stage(
                    job,
                    JobStatus.DEDUPED,
                    f"Deduplicated and split samples (attempt {attempt}/{attempt_count})",
                    lambda current_roundtrip=list(aggregated_roundtrip): self._dedupe_and_split(
                        current_roundtrip,
                        job.request.output_config.target_qa_count,
                        job.request.output_config.split_seed_limit,
                        job.request.output_config.split_gold_limit,
                        paths["releases"],
                    ),
                )
                if len(deduped) >= job.request.output_config.target_qa_count or deduped:
                    break

            self.artifact_store.write_jsonl(paths["skeletons"], [item.model_dump() for item in aggregated_skeletons])
            job.artifacts["skeletons"] = str(paths["skeletons"])
            self.artifact_store.write_jsonl(paths["instantiated"], [item.model_dump() for item in aggregated_candidates])
            job.artifacts["instantiated"] = str(paths["instantiated"])
            self.artifact_store.write_jsonl(paths["validated"], [item.model_dump() for item in aggregated_validated])
            job.artifacts["validated"] = str(paths["validated"])
            self.artifact_store.write_jsonl(paths["qa"], [item.model_dump() for item in aggregated_qa])
            job.artifacts["qa"] = str(paths["qa"])

            if not deduped:
                raise NoValidQAGeneratedError(
                    f"Unable to generate any valid QA after {attempt_count} attempts for job {job_id}."
                )

            self.artifact_store.write_jsonl(paths["releases"], [self._export_sample(item) for item in deduped])
            job.artifacts["releases"] = str(paths["releases"])

            report = self._run_stage(
                job,
                JobStatus.PACKAGED,
                "Packaged artifacts",
                lambda: self._build_report_with_dispatch(
                    deduped,
                    target_qa_count=job.request.output_config.target_qa_count,
                    selection_meta=selection_meta,
                ),
            )
            self.artifact_store.write_json(paths["reports"], report)
            job.artifacts["report"] = str(paths["reports"])
            job.metrics = report

            job.status = JobStatus.COMPLETED
            job.updated_at = self._now()
            self.job_store.save(job)
            return job
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.updated_at = self._now()
            job.errors.append({"code": getattr(exc, "code", "UNEXPECTED_ERROR"), "message": str(exc)})
            self.job_store.save(job)
            return job

    def _apply_roundtrip(self, job: JobRecord, qa_samples: list[QASample], llm_config, mode: str) -> list[QASample]:
        with ThreadPoolExecutor(max_workers=self._parallelism(len(qa_samples), mode)) as executor:
            checks = list(executor.map(lambda sample: self.roundtrip_service.check(sample, llm_config), qa_samples))

        output = []
        for sample, (is_valid, approved_variants, approved_styles) in zip(qa_samples, checks):
            sample.validation.roundtrip_check = is_valid
            sample.question_variants_zh = approved_variants
            sample.question_variant_styles = approved_styles
            if job.request.validation_config.roundtrip_required and not is_valid:
                continue
            output.append(sample)
        return output

    def _dedupe_and_split(
        self,
        samples: list[QASample],
        target_qa_count: int,
        seed_limit: int,
        gold_limit: int,
        release_path,
    ) -> tuple[list[QASample], dict]:
        deduped = self._select_best_by_question(samples)
        history = self.release_history_store.load_signatures(exclude_paths={release_path})
        selected, selection_meta = self._select_release_batch(deduped, history, target_qa_count)
        output = sorted(selected, key=self._sample_sort_key, reverse=True)

        for idx, sample in enumerate(output):
            if idx < seed_limit:
                sample.split = "seed"
            elif idx < seed_limit + gold_limit and sample.difficulty in {"L6", "L7", "L8"}:
                sample.split = "gold"
            else:
                sample.split = "silver"
        return output, selection_meta

    def _dedupe_candidates(self, candidates):
        seen = set()
        output = []
        for candidate in candidates:
            key = normalize_cypher(candidate.cypher)
            if key in seen:
                continue
            seen.add(key)
            output.append(candidate)
        return output

    def _dedupe_validated(self, samples):
        seen = set()
        output = []
        for sample in samples:
            key = normalize_cypher(sample.candidate.cypher)
            if key in seen:
                continue
            seen.add(key)
            output.append(sample)
        return output

    def _sample_quality(self, sample: QASample) -> tuple:
        generation_mode_score = {
            "llm_refine": 3,
            "llm_direct": 2,
            "template": 1,
        }.get(sample.provenance.get("generation_mode", "template"), 0)
        return (
            generation_mode_score,
            1 if sample.validation.roundtrip_check else 0,
            len(sample.question_variants_zh),
            sample.result_signature.row_count,
            int(sample.difficulty[1:]) if sample.difficulty.startswith("L") else 0,
        )

    def _sample_sort_key(self, sample: QASample) -> tuple:
        return self._sample_quality(sample)

    def _select_best_by_question(self, samples: list[QASample]) -> list[QASample]:
        by_question: dict[str, QASample] = {}
        for sample in samples:
            key = sample.question_canonical_zh.strip()
            current = by_question.get(key)
            if current is None or self._sample_quality(sample) > self._sample_quality(current):
                by_question[key] = sample
        return list(by_question.values())

    def _select_release_batch(self, samples: list[QASample], history: dict[str, set[str]], target_qa_count: int) -> tuple[list[QASample], dict]:
        history_questions = history.get("questions", set())
        history_cyphers = history.get("cyphers", set())
        fresh_pool = []
        repeated_pool = []
        for sample in samples:
            question_key = normalize_question(sample.question_canonical_zh)
            cypher_key = normalize_cypher(sample.cypher)
            if question_key in history_questions or cypher_key in history_cyphers:
                repeated_pool.append(sample)
            else:
                fresh_pool.append(sample)

        selected = self._greedy_diverse_pick(fresh_pool, target_qa_count)
        if len(selected) < target_qa_count:
            selected_keys = {
                (normalize_question(sample.question_canonical_zh), normalize_cypher(sample.cypher))
                for sample in selected
            }
            fallback = [
                sample
                for sample in repeated_pool
                if (normalize_question(sample.question_canonical_zh), normalize_cypher(sample.cypher)) not in selected_keys
            ]
            selected.extend(self._greedy_diverse_pick(fallback, target_qa_count - len(selected), selected))

        return selected[:target_qa_count], {
            "requested_count": target_qa_count,
            "candidate_count": len(samples),
            "history_skipped_count": len(repeated_pool),
            "fresh_candidate_count": len(fresh_pool),
            "selected_count": min(len(selected), target_qa_count),
        }

    def _greedy_diverse_pick(self, pool: list[QASample], target_count: int, seed: list[QASample] | None = None) -> list[QASample]:
        remaining = list(pool)
        selected = list(seed or [])
        output: list[QASample] = []
        while remaining and len(output) < target_count:
            best = max(remaining, key=lambda sample: self._diversity_score(selected + output, sample))
            output.append(best)
            remaining.remove(best)
        return output

    def _diversity_score(self, selected: list[QASample], sample: QASample) -> tuple:
        selected_query_types = {query_type for item in selected for query_type in item.query_types}
        selected_families = {item.provenance.get("structure_family", "") for item in selected}
        selected_difficulties = {item.difficulty for item in selected}
        selected_modes = {item.provenance.get("generation_mode", "") for item in selected}
        novel_query_types = sum(1 for query_type in sample.query_types if query_type not in selected_query_types)
        novel_family = 1 if sample.provenance.get("structure_family", "") not in selected_families else 0
        novel_difficulty = 1 if sample.difficulty not in selected_difficulties else 0
        novel_mode = 1 if sample.provenance.get("generation_mode", "") not in selected_modes else 0
        return (
            novel_query_types,
            novel_family,
            novel_difficulty,
            novel_mode,
            *self._sample_quality(sample),
        )

    def _effective_limits(self, request: JobRequest):
        limits = request.generation_limits.model_copy(deep=True)
        if request.mode.value == "online":
            limits.max_skeletons = min(limits.max_skeletons, 8)
            limits.max_candidates_per_skeleton = 1
            limits.max_variants_per_question = min(limits.max_variants_per_question, 5)
        return limits

    def _effective_llm_config(self, request: JobRequest):
        llm_config = request.llm_config.model_copy(deep=True)
        if request.mode.value == "online":
            llm_config.max_output_tokens = min(llm_config.max_output_tokens, 300)
        return llm_config

    def _generate_questions(self, validated, schema, llm_config, max_variants, mode: str):
        with ThreadPoolExecutor(max_workers=self._parallelism(len(validated), mode)) as executor:
            results = list(
                executor.map(
                    lambda item: self._safe_generate_question(
                        item,
                        schema,
                        llm_config,
                        max_variants,
                    ),
                    validated,
                )
            )
        return [item for item in results if item is not None]

    def _safe_generate_question(self, validated_sample, schema, llm_config, max_variants):
        try:
            return self.question_service.generate(
                validated_sample,
                schema,
                llm_config,
                max_variants,
            )
        except Exception:
            return None

    def _parallelism(self, item_count: int, mode: str) -> int:
        if item_count <= 1:
            return 1
        ceiling = 2
        return min(ceiling, item_count)

    def _export_sample(self, sample: QASample) -> dict:
        return {
            "id": sample.id,
            "question": sample.question_canonical_zh,
            "cypher": sample.cypher,
            "answer": sample.answer,
            "difficulty": sample.difficulty,
        }

    def _build_report_with_dispatch(self, samples: list[QASample], target_qa_count: int, selection_meta: dict) -> dict:
        report = self.report_builder.build(samples)
        report["selection"] = {
            **selection_meta,
            "requested_count": target_qa_count,
            "final_count": len(samples),
        }
        report["dispatch"] = self.qa_dispatcher.dispatch_samples(samples)
        return report

    def _run_stage(self, job: JobRecord, status: JobStatus, summary: str, fn):
        previous = job.status
        record = StageRecord(from_status=previous, to_status=status, summary=summary)
        start = datetime.now(timezone.utc)
        payload = fn()
        end = datetime.now(timezone.utc)
        record.finished_at = end.isoformat()
        record.duration_ms = int((end - start).total_seconds() * 1000)
        job.status = status
        job.updated_at = self._now()
        job.stages.append(record)
        self.job_store.save(job)
        return payload

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
