from __future__ import annotations

from enum import Enum


class StageName(str, Enum):
    GRAPH_MODEL_LOADER = "graph_model_loader"
    INPUT_CLARIFICATION_GATE = "input_clarification_gate"
    QUESTION_DECOMPOSER = "question_decomposer"
    CANDIDATE_RETRIEVAL = "candidate_retrieval"
    CANDIDATE_RERANKER = "candidate_reranker"
    LITERAL_RESOLVER = "literal_resolver"
    DETERMINISTIC_ASSEMBLER = "deterministic_assembler"
    GROUNDED_UNDERSTANDING = "grounded_understanding"
    SEMANTIC_BINDER = "semantic_binder"
    SEMANTIC_VALIDATOR = "semantic_validator"
    REPAIR_CONTROLLER = "repair_controller"
    DSL_BUILDER = "dsl_builder"
    DSL_STRUCTURAL_COVERAGE_GATE = "dsl_structural_coverage_gate"
    DSL_PARSER = "dsl_parser"
    CYPHER_COMPILER = "cypher_compiler"
    CYPHER_SELF_VALIDATION = "cypher_self_validation"
    OUTPUT = "output"


class StageStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
