# Structural Coverage Design For Text2Cypher QA Generation

## Goal

Build the next expansion layer of the local Text2Cypher QA agent so it can cover the full primary query-type space defined in [text2cypher_qa_generation_scheme.md](/Users/wangxinhao/muti-agent-offline-system/text2cypher_qa_generation_scheme.md), instead of only producing a small set of difficulty-oriented skeletons.

This phase does not attempt full Cypher grammar coverage. It focuses on complete coverage of the documented primary query-type space:

- `LOOKUP`
- `FILTER`
- `SORT_TOPK`
- `AGGREGATION`
- `GROUP_AGG`
- `MULTI_HOP`
- `COMPARISON`
- `TEMPORAL`
- `PATH`
- `SET_OP`
- `SUBQUERY`
- `HYBRID`

The outcome of this phase is a generator that is explicitly organized by query type, structure family, difficulty level, and language-style coverage.

## Why This Phase Exists

The current system already has:

- deterministic `L1-L8` difficulty classification
- difficulty-first generation families
- language-style coverage for question variants
- runtime execution and export pipeline

What it does not yet have is structural completeness. Right now, structural coverage is limited to a small family list. That means the system can produce valid QA pairs, but it cannot yet claim that it systematically covers the main Text2Cypher query space.

The missing capability is not more random templates. The missing capability is a stronger generator organization model.

## Recommended Architecture

The generator will move from:

`difficulty family -> candidate -> validation`

to:

`query_type -> structure_family -> difficulty variant -> candidate -> validation -> language variants`

This keeps responsibilities separate:

- query type answers "what kind of question is this?"
- structure family answers "what structural Cypher pattern does it use?"
- difficulty answers "how complex is that structure in this instance?"
- language styles answer "how many natural-language surfaces does this query support?"

This structure is the smallest architecture that can support structural completeness without collapsing into an unmaintainable template list.

## Core Design

## 1. Query-Type Registry

Introduce a registry of the 12 primary query types. Each query type owns one or more structure families.

Each query type entry will define:

- `query_type_id`
- `display_name`
- `description`
- `families`

This registry becomes the source of truth for generation and reporting.

## 2. Structure Families

Each query type will have 3 to 6 structure families depending on natural diversity and implementation cost. A structure family is narrower than a query type and broader than a single template.

Examples:

### `LOOKUP`

- `lookup_node_return`
- `lookup_property_projection`
- `lookup_entity_detail`

### `FILTER`

- `filter_single_condition`
- `filter_boolean_combo`
- `filter_range_condition`

### `SORT_TOPK`

- `sort_single_metric`
- `topk_entities`
- `sorted_filtered_projection`

### `AGGREGATION`

- `aggregate_global_count`
- `aggregate_filtered_count`
- `aggregate_scalar_metric`

### `GROUP_AGG`

- `group_count`
- `group_ranked_count`
- `group_filtered_aggregate`

### `MULTI_HOP`

- `two_hop_return`
- `two_hop_filtered`
- `multi_hop_projection`

### `COMPARISON`

- `attribute_comparison`
- `aggregate_comparison`
- `rank_position_comparison`

### `TEMPORAL`

- `time_range_filter`
- `recent_or_earliest`
- `temporal_ordering`

### `PATH`

- `path_existence`
- `variable_length_path`
- `path_constrained_target`

### `SET_OP`

- `distinct_projection`
- `set_like_union_projection`
- `membership_intersection_style`

### `SUBQUERY`

- `with_stage_filter`
- `with_stage_aggregate`
- `two_stage_refine`

### `HYBRID`

- `temporal_aggregate_hybrid`
- `path_aggregate_hybrid`
- `comparison_subquery_hybrid`

Each family is implemented as one or more executable skeleton templates with well-defined slot constraints.

## 3. Difficulty Layering

Difficulty remains mandatory, but it no longer defines the top-level generator shape.

Instead:

- every family declares an allowed difficulty band
- the instantiated candidate is classified by the deterministic difficulty classifier
- only candidates whose classified difficulty fits the family band are retained

Examples:

- `lookup_node_return` is typically `L1`
- `filter_boolean_combo` may reach `L2-L4`
- `two_hop_filtered` may land in `L5-L6`
- `with_stage_aggregate` may land in `L7-L8`

This keeps difficulty grounded in structure rather than in arbitrary labeling.

## 4. Validation Model

Validation must now confirm three kinds of correctness:

1. `structural validity`
   The Cypher executes and respects schema/value/runtime constraints.

2. `difficulty validity`
   The deterministic classifier agrees with the generated candidate difficulty.

3. `query-type validity`
   The generated candidate belongs to the expected query type and structure family.

For this phase, query-type validity is implemented by rule-based family metadata checks, not by an LLM classifier. The family itself defines the required structural markers:

- hop count
- aggregation presence
- ordering
- temporal predicates
- `WITH`
- variable-length path usage
- set-like projection behavior

If a candidate violates the family contract, it is rejected before question generation.

## 5. Coverage Reporting

The report layer will gain two new top-level coverage sections:

- `query_type_coverage`
- `structure_family_coverage`

### `query_type_coverage`

Tracks:

- all configured query types
- covered query types
- missing query types
- per-type sample counts
- completeness flag

### `structure_family_coverage`

Tracks:

- all configured families
- covered families
- missing families
- per-family sample counts
- completeness flag

These are reported alongside existing:

- `difficulty_coverage`
- `language_coverage`

The intent is that the system can tell us exactly which structural regions remain missing.

## 6. Generator Behavior

The generator will no longer stop after producing one sample per family in online mode by accident of limited taxonomy.

Instead:

- online mode should sample a compact but diverse set across query types
- offline mode should sweep all query types and all families up to configured limits

The online default should bias toward one representative candidate per family across as many query types as possible, not toward repeated candidates within the same family.

## 7. Export Behavior

The external QA export format remains minimal:

- `id`
- `question`
- `cypher`
- `answer`
- `difficulty`

Structural and language coverage metadata stay inside internal artifacts and reports rather than the minimal release file.

This keeps the agent-facing dataset simple while preserving internal governance.

## 8. Frontend Behavior

The frontend does not need a new workflow. It needs better observability.

Job and import detail views should display:

- query-type coverage
- structure-family coverage
- difficulty coverage
- language coverage

Coverage displays should make missing regions obvious so the operator can tell whether a generation run is structurally complete.

## 9. Error Handling

New rejection reasons should be explicit:

- `QUERY_TYPE_MISMATCH`
- `STRUCTURE_FAMILY_MISMATCH`
- `TEMPORAL_BINDING_ERROR`
- `PATH_PATTERN_INVALID`
- `SUBQUERY_STAGE_INVALID`

These errors belong in internal reports and help identify which family definitions are under-specified.

## 10. Testing Strategy

This phase needs three layers of verification:

### Unit tests

- query-type registry integrity
- family metadata validation
- family-to-difficulty compatibility
- structural predicate detection

### Pipeline tests

- generated samples cover all registered query types
- generated samples expose structure-family coverage
- report includes query-type and family coverage
- exports remain minimal

### Regression tests

- difficulty classifier still passes all existing boundary tests
- language coverage still appears in final metrics
- online mode remains fast enough for local use

## 11. Scope Boundaries

This phase includes:

- full primary query-type coverage
- explicit structure families
- family-aware validation
- coverage reporting
- frontend visibility

This phase does not include:

- full Cypher grammar coverage
- arbitrary user-authored family plugins
- large-scale distributed generation
- LLM-based query-type classification

## 12. Definition Of Done

This phase is complete when:

1. every primary query type from the scheme document has at least one runnable structure family
2. reports expose complete `query_type_coverage`
3. reports expose complete `structure_family_coverage`
4. generated samples still pass difficulty and language coverage checks
5. frontend detail views surface the new structural coverage metrics
6. the pipeline tests and frontend build pass

## 13. Recommendation

Implement this as a generator refactor, not as scattered template additions.

The correct long-term order is:

`query type -> family -> difficulty -> language`

That gives the project one stable axis for structure, one stable axis for complexity, and one stable axis for natural-language diversity.
