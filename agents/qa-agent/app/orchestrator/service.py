from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.domain.coverage.service import CoverageService
from app.domain.generation.service import GenerationService
from app.domain.query_plan.service import QueryPlanService
from app.domain.models import JobRecord, JobRequest, JobStatus, QASample, StageRecord
from app.domain.questioning.service import QuestionService, normalize_cypher, normalize_question
from app.domain.roundtrip.service import RoundtripService
from app.domain.schema.compatibility_service import SchemaCompatibilityService
from app.domain.schema.service import SchemaService
from app.domain.schema.source_resolver import SourceResolver
from app.domain.validation.service import ValidationService
from app.integrations.qa_dispatcher import QADispatcher
from app.logging import ModuleLogStore
from app.reports.builder import ReportBuilder
from app.reports.business_stages import build_business_stage_summary
from app.storage.artifact_store import ArtifactStore
from app.storage.job_log_store import JobLogStore
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
        coverage_service: CoverageService | None = None,
        generation_service: GenerationService | None = None,
        query_plan_service: QueryPlanService | None = None,
        validation_service: ValidationService | None = None,
        question_service: QuestionService | None = None,
        roundtrip_service: RoundtripService | None = None,
        report_builder: ReportBuilder | None = None,
        qa_dispatcher: QADispatcher | None = None,
        job_log_store: JobLogStore | None = None,
        release_history_store: ReleaseHistoryStore | None = None,
        module_logs: ModuleLogStore | None = None,
    ) -> None:
        self.job_store = job_store or JobStore()
        self.artifact_store = artifact_store or ArtifactStore()
        self.job_log_store = job_log_store or JobLogStore(root=(self.artifact_store.root / "logs"))
        self.schema_service = schema_service or SchemaService()
        self.source_resolver = source_resolver or SourceResolver()
        self.schema_compatibility_service = schema_compatibility_service or SchemaCompatibilityService()
        self.coverage_service = coverage_service or CoverageService()
        self.generation_service = generation_service or GenerationService()
        self.query_plan_service = query_plan_service or QueryPlanService()
        self.validation_service = validation_service or ValidationService()
        self.question_service = question_service or QuestionService()
        self.roundtrip_service = roundtrip_service or RoundtripService()
        self.report_builder = report_builder or ReportBuilder()
        self.module_logs = module_logs or ModuleLogStore()
        self.qa_dispatcher = qa_dispatcher or QADispatcher(module_logs=self.module_logs)
        self.release_history_store = release_history_store or ReleaseHistoryStore(
            root=(artifact_store.root / "releases") if artifact_store else None
        )

    def create_job(self, request: JobRequest) -> JobRecord:
        job = JobRecord(request=request)
        job.metrics["business_stages"] = self._build_business_stage_summary(job)
        self.job_store.save(job)
        self.module_logs.append(
            module="api",
            level="info",
            operation="job_created",
            trace_id=job.job_id,
            status="success",
            request_body={"mode": request.mode.value, "target_qa_count": request.output_config.target_qa_count},
            response_body={"job_id": job.job_id},
        )
        return job

    def create_and_run_job(self, request: JobRequest) -> JobRecord:
        job = self.create_job(request)
        return self.run_job(job.job_id)

    def get_job_snapshot(self, job_id: str) -> JobRecord:
        job = self.job_store.get(job_id)
        return self._hydrate_job_metrics(job)

    def list_job_snapshots(self) -> list[JobRecord]:
        return [self._hydrate_job_metrics(job) for job in self.job_store.list()]

    def delete_job(self, job_id: str) -> None:
        job = self.job_store.get(job_id)
        self.artifact_store.delete_paths(job.artifacts.values())
        self.job_log_store.delete(job_id)
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
        self.module_logs.append(
            module="redispatch",
            level="info",
            operation="job_redispatch_requested",
            trace_id=job_id,
            status="started",
            request_body={"job_id": job_id, "row_count": len(rows)},
        )
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
        self.module_logs.append(
            module="redispatch",
            level="info",
            operation="job_redispatch_completed",
            trace_id=job_id,
            status=dispatch_result.get("status"),
            response_body={"job_id": job_id, "dispatch": dispatch_result},
        )
        return job

    def run_job(self, job_id: str) -> JobRecord:
        job = self.job_store.get(job_id)
        job.status = JobStatus.CREATED
        job.stages = []
        job.metrics = {}
        job.errors = []
        job.updated_at = self._now()
        self.job_store.save(job)
        paths = self.artifact_store.ensure_job_dirs(job_id)
        limits = self._effective_limits(job.request)
        llm_config = self._effective_llm_config(job.request)
        llm_cypher_enabled = self._should_enable_llm_cypher_enrichment(
            job.request.output_config.target_qa_count,
            job.request.mode.value,
        )

        try:
            self.job_log_store.append(job.job_id, "system", "info", "job started", {"mode": job.request.mode.value})
            self.module_logs.append(
                module="generation",
                level="info",
                operation="job_started",
                trace_id=job.job_id,
                status="started",
                request_body={"job_id": job.job_id, "mode": job.request.mode.value},
            )
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
                coverage_specs = self.coverage_service.build_specs(
                    schema=schema,
                    limits=limits,
                    target_qa_count=self._query_plan_target_count(job.request.output_config.target_qa_count, limits.max_skeletons),
                    difficulty_targets=job.request.output_config.difficulty_targets,
                    diversity_key=diversity_key,
                )
                skeletons = self._run_stage(
                    job,
                    JobStatus.SKELETON_READY,
                    f"Built coverage specs (attempt {attempt}/{attempt_count})",
                    lambda current_coverage_specs=coverage_specs: current_coverage_specs,
                )
                aggregated_skeletons.extend(skeletons)

                candidates = self._run_stage(
                    job,
                    JobStatus.CYPHER_READY,
                    f"Instantiated candidates (attempt {attempt}/{attempt_count})",
                    lambda current_coverage_specs=skeletons: self._dedupe_candidates(
                        self._instantiate_candidates_from_specs(
                            schema,
                            limits,
                            llm_config if llm_cypher_enabled else None,
                            current_coverage_specs,
                        )
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
                            item.validation.plan_valid,
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
                    lambda current_validated=self._shortlist_validated_samples(
                        list(aggregated_validated),
                        job.request.output_config.target_qa_count,
                        job.request.output_config.difficulty_targets,
                    ): self._select_best_by_question(
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
                        job.request.output_config.difficulty_targets,
                    ),
                )
                if (
                    job.request.mode.value == "online"
                    and deduped
                    and (
                        not job.request.output_config.difficulty_targets
                        or self._difficulty_targets_satisfied(deduped, job.request.output_config.difficulty_targets)
                    )
                ):
                    break
                if (
                    len(deduped) >= job.request.output_config.target_qa_count
                    and int(selection_meta.get("fresh_candidate_count", 0)) >= job.request.output_config.target_qa_count
                ):
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
            if job.request.output_config.difficulty_targets and not self._difficulty_targets_satisfied(
                deduped,
                job.request.output_config.difficulty_targets,
            ):
                raise NoValidQAGeneratedError(
                    "Unable to satisfy requested difficulty distribution after "
                    f"{attempt_count} attempts: {selection_meta.get('difficulty_shortfalls', {})}"
                )

            self.artifact_store.write_jsonl(paths["releases"], [self._export_sample(item) for item in deduped])
            job.artifacts["releases"] = str(paths["releases"])

            report = self._run_stage(
                job,
                JobStatus.PACKAGED,
                "Packaged artifacts",
                lambda: self._build_report_with_dispatch(
                    job,
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
            self.job_log_store.append(job.job_id, "system", "info", "job completed", {"final_count": len(deduped)})
            self.module_logs.append(
                module="generation",
                level="info",
                operation="job_completed",
                trace_id=job.job_id,
                status="success",
                response_body={"job_id": job.job_id, "final_count": len(deduped)},
            )
            return job
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.updated_at = self._now()
            job.errors.append({"code": getattr(exc, "code", "UNEXPECTED_ERROR"), "message": str(exc)})
            job.metrics["business_stages"] = self._build_business_stage_summary(job)
            self.job_store.save(job)
            self.job_log_store.append(
                job.job_id,
                "system",
                "error",
                "job failed",
                {"code": getattr(exc, "code", "UNEXPECTED_ERROR"), "message": str(exc)},
            )
            self.module_logs.append(
                module="generation",
                level="error",
                operation="job_failed",
                trace_id=job.job_id,
                status="failed",
                response_body={"code": getattr(exc, "code", "UNEXPECTED_ERROR"), "message": str(exc)},
            )
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
        if job.request.validation_config.roundtrip_required and job.request.output_config.difficulty_targets:
            present = self._difficulty_counts(output)
            for level, target_count in job.request.output_config.difficulty_targets.items():
                needed = target_count - present.get(level, 0)
                if needed <= 0:
                    continue
                fallback = [
                    sample
                    for sample, (is_valid, _, _) in zip(qa_samples, checks)
                    if not is_valid and sample.difficulty == level and self._has_trusted_canonical_qa(sample)
                ]
                for sample in fallback[:needed]:
                    output.append(sample)
                    present[level] = present.get(level, 0) + 1
        return output

    def _has_trusted_canonical_qa(self, sample: QASample) -> bool:
        if not sample.answer:
            return False
        try:
            canonical_pass = bool(json.loads(sample.provenance.get("canonical_pass", "false")))
        except json.JSONDecodeError:
            canonical_pass = sample.provenance.get("canonical_pass", "").strip().lower() == "true"
        try:
            canonical_checks = json.loads(sample.provenance.get("canonical_checks", "{}"))
        except json.JSONDecodeError:
            canonical_checks = {}
        checks_pass = isinstance(canonical_checks, dict) and all(bool(value) for value in canonical_checks.values())
        rule_check = getattr(self.roundtrip_service, "_passes_rule_checks", None)
        rules_pass = bool(rule_check(sample.question_canonical_zh, sample.cypher)) if callable(rule_check) else True
        return canonical_pass and checks_pass and rules_pass

    def _dedupe_and_split(
        self,
        samples: list[QASample],
        target_qa_count: int,
        seed_limit: int,
        gold_limit: int,
        release_path,
        difficulty_targets: dict[str, int] | None = None,
    ) -> tuple[list[QASample], dict]:
        deduped = self._select_best_by_question(samples)
        history = self.release_history_store.load_signatures(exclude_paths={release_path})
        selected, selection_meta = self._select_release_batch(deduped, history, target_qa_count, difficulty_targets)
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
            1 if sample.answer else 0,
            generation_mode_score,
            1 if sample.validation.roundtrip_check else 0,
            len(sample.question_variants_zh),
            sample.result_signature.row_count,
            int(sample.difficulty[1:]) if sample.difficulty.startswith("L") else 0,
        )

    def _sample_sort_key(self, sample: QASample) -> tuple:
        return self._sample_quality(sample)

    def _validated_quality(self, sample) -> tuple:
        generation_mode_score = {
            "llm_refine": 3,
            "llm_direct": 2,
            "template": 1,
        }.get(sample.candidate.generation_mode, 0)
        difficulty = sample.classified_difficulty or sample.candidate.difficulty
        return (
            generation_mode_score,
            sample.result_signature.row_count,
            int(difficulty[1:]) if difficulty.startswith("L") else 0,
        )

    def _validated_shortlist_budget(self, target_qa_count: int, validated_count: int) -> int:
        if validated_count <= target_qa_count:
            return validated_count
        if target_qa_count <= 1:
            return min(validated_count, 3)
        if target_qa_count <= 3:
            return min(validated_count, target_qa_count + 2)
        if target_qa_count <= 10:
            return min(validated_count, target_qa_count + 3)
        return min(validated_count, target_qa_count + 5)

    def _shortlist_validated_samples(self, samples, target_qa_count: int, difficulty_targets: dict[str, int] | None = None):
        if difficulty_targets:
            selected = []
            selected_keys = set()
            for difficulty, count in sorted(difficulty_targets.items(), key=lambda item: int(item[0][1:])):
                pool = [
                    sample
                    for sample in samples
                    if (sample.classified_difficulty or sample.candidate.difficulty) == difficulty
                ]
                for sample in self._shortlist_validated_samples(pool, count + 2):
                    key = normalize_cypher(sample.candidate.cypher)
                    if key not in selected_keys:
                        selected.append(sample)
                        selected_keys.add(key)
            return selected

        budget = self._validated_shortlist_budget(target_qa_count, len(samples))
        if budget >= len(samples):
            return samples

        remaining = sorted(samples, key=self._validated_quality, reverse=True)
        selected = []
        while remaining and len(selected) < budget:
            best = max(remaining, key=lambda sample: self._validated_diversity_score(selected, sample))
            selected.append(best)
            remaining.remove(best)
        return selected

    def _validated_diversity_score(self, selected, sample) -> tuple:
        selected_query_types = {item.candidate.query_types[0] for item in selected if item.candidate.query_types}
        selected_families = {item.candidate.structure_family for item in selected}
        selected_difficulties = {item.classified_difficulty or item.candidate.difficulty for item in selected}
        selected_modes = {item.candidate.generation_mode for item in selected}
        query_type = sample.candidate.query_types[0] if sample.candidate.query_types else ""
        difficulty = sample.classified_difficulty or sample.candidate.difficulty
        return (
            1 if query_type not in selected_query_types else 0,
            1 if sample.candidate.structure_family not in selected_families else 0,
            1 if difficulty not in selected_difficulties else 0,
            1 if sample.candidate.generation_mode not in selected_modes else 0,
            self._validated_quality(sample),
        )

    def _select_best_by_question(self, samples: list[QASample]) -> list[QASample]:
        by_question: dict[str, QASample] = {}
        for sample in samples:
            key = sample.question_canonical_zh.strip()
            current = by_question.get(key)
            if current is None or self._sample_quality(sample) > self._sample_quality(current):
                by_question[key] = sample
        return list(by_question.values())

    def _select_release_batch(
        self,
        samples: list[QASample],
        history: dict[str, set[str]],
        target_qa_count: int,
        difficulty_targets: dict[str, int] | None = None,
    ) -> tuple[list[QASample], dict]:
        samples = [sample for sample in samples if self._has_non_empty_answer(sample)]
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

        if difficulty_targets:
            selected = self._select_release_batch_by_difficulty(fresh_pool, difficulty_targets)
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
                selected.extend(self._select_release_batch_by_difficulty(fallback, difficulty_targets, selected))
            selected = selected[:target_qa_count]
            selected_counts = self._difficulty_counts(selected)
            difficulty_shortfalls = {
                level: count - selected_counts.get(level, 0)
                for level, count in difficulty_targets.items()
                if selected_counts.get(level, 0) < count
            }
            return selected, {
                "requested_count": target_qa_count,
                "candidate_count": len(samples),
                "history_skipped_count": len(repeated_pool),
                "fresh_candidate_count": len(fresh_pool),
                "selected_count": len(selected),
                "difficulty_targets": dict(difficulty_targets),
                "selected_difficulty_counts": selected_counts,
                "difficulty_shortfalls": difficulty_shortfalls,
            }

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

    def _has_non_empty_answer(self, sample: QASample) -> bool:
        return bool(sample.answer)

    def _select_release_batch_by_difficulty(
        self,
        pool: list[QASample],
        difficulty_targets: dict[str, int],
        seed: list[QASample] | None = None,
    ) -> list[QASample]:
        selected = list(seed or [])
        output: list[QASample] = []
        for difficulty, target_count in sorted(difficulty_targets.items(), key=lambda item: int(item[0][1:])):
            already_selected = sum(1 for sample in selected + output if sample.difficulty == difficulty)
            needed = max(0, target_count - already_selected)
            if needed <= 0:
                continue
            candidates = [sample for sample in pool if sample.difficulty == difficulty]
            output.extend(self._greedy_diverse_pick(candidates, needed, selected + output))
        return output

    def _difficulty_counts(self, samples: list[QASample]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sample in samples:
            counts[sample.difficulty] = counts.get(sample.difficulty, 0) + 1
        return counts

    def _difficulty_targets_satisfied(self, samples: list[QASample], difficulty_targets: dict[str, int]) -> bool:
        counts = self._difficulty_counts(samples)
        return all(counts.get(level, 0) >= count for level, count in difficulty_targets.items())

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
        target = request.output_config.target_qa_count
        if target >= 10:
            limits.max_candidates_per_skeleton = min(limits.max_candidates_per_skeleton, 2)
        if target >= 20:
            limits.max_variants_per_question = min(limits.max_variants_per_question, 1)
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
        try:
            return self.question_service.generate_batch(validated, schema, llm_config, max_variants)
        except Exception:
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
        ceiling = 3 if mode == "online" else 6
        return min(ceiling, item_count)

    def _query_plan_target_count(self, target_qa_count: int, max_skeletons: int) -> int:
        if max_skeletons <= 8:
            return max_skeletons
        if target_qa_count <= 1:
            return min(max_skeletons, 3)
        if target_qa_count <= 3:
            return min(max_skeletons, target_qa_count + 2)
        if target_qa_count <= 5:
            return min(max_skeletons, target_qa_count + 3)
        if target_qa_count <= 10:
            return min(max_skeletons, target_qa_count + 4)
        return min(max_skeletons, target_qa_count + 8)

    def _should_enable_llm_cypher_enrichment(self, target_qa_count: int, mode: str) -> bool:
        if mode == "online":
            return True
        return target_qa_count < 20

    def _export_sample(self, sample: QASample) -> dict:
        return {
            "id": sample.id,
            "question": sample.question_canonical_zh,
            "cypher": sample.cypher,
            "answer": sample.answer,
            "difficulty": sample.difficulty,
        }

    def _build_report_with_dispatch(
        self,
        job: JobRecord,
        samples: list[QASample],
        target_qa_count: int,
        selection_meta: dict,
    ) -> dict:
        dispatch_result = self.qa_dispatcher.dispatch_samples(samples)
        report = self.report_builder.build(samples, stages=job.stages, dispatch=dispatch_result)
        report["selection"] = {
            **selection_meta,
            "requested_count": target_qa_count,
            "final_count": len(samples),
        }
        report["dispatch"] = dispatch_result
        report["performance"] = self._build_performance_summary(report.get("business_stages", []), len(samples))
        return report

    def _run_stage(self, job: JobRecord, status: JobStatus, summary: str, fn):
        previous = job.status
        record = StageRecord(from_status=previous, to_status=status, summary=summary)
        start = datetime.now(timezone.utc)
        business_stage = self._internal_to_business_stage(status)
        job.status = status
        job.updated_at = self._now()
        job.stages.append(record)
        job.metrics["business_stages"] = self._build_business_stage_summary(job)
        self.job_store.save(job)
        self.job_log_store.append(job.job_id, business_stage, "info", f"started: {summary}", {"internal_status": status.value, "from_status": previous.value if previous else None})
        self.module_logs.append(
            module="generation",
            level="info",
            operation="stage_started",
            trace_id=job.job_id,
            status="started",
            request_body={
                "job_id": job.job_id,
                "business_stage": business_stage,
                "internal_status": status.value,
                "summary": summary,
            },
        )
        try:
            payload = fn()
        except Exception as exc:  # noqa: BLE001
            end = datetime.now(timezone.utc)
            record.finished_at = end.isoformat()
            record.duration_ms = int((end - start).total_seconds() * 1000)
            record.error_code = getattr(exc, "code", "UNEXPECTED_ERROR")
            record.error_message = str(exc)
            job.updated_at = self._now()
            job.metrics["business_stages"] = self._build_business_stage_summary(job)
            self.job_store.save(job)
            self.job_log_store.append(job.job_id, business_stage, "error", f"failed: {summary}", {"internal_status": status.value, "error": str(exc)})
            self.module_logs.append(
                module="generation",
                level="error",
                operation="stage_failed",
                trace_id=job.job_id,
                status="failed",
                request_body={
                    "job_id": job.job_id,
                    "business_stage": business_stage,
                    "internal_status": status.value,
                    "summary": summary,
                },
                response_body={"error": str(exc)},
            )
            raise
        end = datetime.now(timezone.utc)
        record.finished_at = end.isoformat()
        record.duration_ms = int((end - start).total_seconds() * 1000)
        job.updated_at = self._now()
        job.metrics["business_stages"] = self._build_business_stage_summary(job)
        self.job_store.save(job)
        self.job_log_store.append(job.job_id, business_stage, "info", f"completed: {summary}", {"internal_status": status.value, "duration_ms": record.duration_ms})
        self.module_logs.append(
            module="generation",
            level="info",
            operation="stage_completed",
            trace_id=job.job_id,
            status="success",
            request_body={
                "job_id": job.job_id,
                "business_stage": business_stage,
                "internal_status": status.value,
                "summary": summary,
            },
            response_body={"duration_ms": record.duration_ms},
        )
        return payload

    def _build_business_stage_summary(self, job: JobRecord) -> list[dict]:
        return build_business_stage_summary(job.stages, job.metrics.get("dispatch"))

    def _hydrate_job_metrics(self, job: JobRecord) -> JobRecord:
        hydrated = job.model_copy(deep=True)
        hydrated.metrics["business_stages"] = self._build_business_stage_summary(hydrated)
        sample_count = int((hydrated.metrics.get("selection") or {}).get("final_count") or hydrated.metrics.get("sample_count") or 0)
        hydrated.metrics["performance"] = self._build_performance_summary(
            hydrated.metrics["business_stages"],
            sample_count,
        )
        return hydrated

    def _build_skeletons(self, schema, limits, diversity_key, query_plans):
        parameters = inspect.signature(self.generation_service.build_skeletons).parameters
        if "query_plans" in parameters:
            return self.generation_service.build_skeletons(
                schema,
                limits,
                diversity_key=diversity_key,
                query_plans=query_plans,
            )
        return self.generation_service.build_skeletons(
            schema,
            limits,
            diversity_key=diversity_key,
        )

    def _instantiate_candidates(self, schema, skeletons, limits, llm_config, query_plans):
        parameters = inspect.signature(self.generation_service.instantiate_candidates).parameters
        if "query_plans" in parameters:
            return self.generation_service.instantiate_candidates(
                schema,
                skeletons,
                limits,
                llm_config,
                query_plans=query_plans,
            )
        return self.generation_service.instantiate_candidates(
            schema,
            skeletons,
            limits,
            llm_config,
        )

    def _instantiate_candidates_from_specs(self, schema, limits, llm_config, coverage_specs):
        parameters = inspect.signature(self.generation_service.instantiate_candidates_from_specs).parameters
        if "model_config" in parameters:
            return self.generation_service.instantiate_candidates_from_specs(
                schema,
                coverage_specs,
                limits,
                model_config=llm_config,
            )
        return self.generation_service.instantiate_candidates_from_specs(
            schema,
            coverage_specs,
            limits,
        )

    def _internal_to_business_stage(self, status: JobStatus) -> str:
        if status == JobStatus.SCHEMA_READY:
            return "ground_schema"
        if status == JobStatus.SKELETON_READY:
            return "spec_coverage"
        if status == JobStatus.CYPHER_READY:
            return "generate_cypher"
        if status == JobStatus.VALIDATED:
            return "tugraph_validate"
        if status == JobStatus.QUESTIONS_READY:
            return "generate_qa"
        if status == JobStatus.ROUNDTRIP_DONE:
            return "roundtrip_check"
        if status == JobStatus.PACKAGED:
            return "release_dispatch"
        if status == JobStatus.DEDUPED:
            return "release_dispatch"
        return "system"

    def _build_performance_summary(self, business_stages: list[dict], qa_count: int) -> dict:
        non_llm_keys = {"ground_schema", "spec_coverage", "generate_cypher", "tugraph_validate", "release_dispatch"}
        non_llm_total_ms = sum(int(stage.get("duration_ms") or 0) for stage in business_stages if stage.get("key") in non_llm_keys)
        return {
            "non_llm_total_ms": non_llm_total_ms,
            "non_llm_per_qa_ms": int(non_llm_total_ms / max(qa_count, 1)),
            "qa_count": qa_count,
            "target_non_llm_per_qa_ms": 10000,
        }

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
