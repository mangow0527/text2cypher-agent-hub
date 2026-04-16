from __future__ import annotations


ALLOWED_DIFFICULTY_LEVELS = {f"L{level}" for level in range(1, 9)}


def _markers(
    *,
    min_hops: int = 0,
    max_hops: int = 0,
    filtering: bool = False,
    aggregation: bool = False,
    grouping: bool = False,
    ordering: bool = False,
    with_stage: bool = False,
    variable_length: bool = False,
    temporal: bool = False,
    distinct: bool = False,
    comparison: bool = False,
    set_op: bool = False,
    subquery: bool = False,
) -> dict:
    return {
        "min_hops": min_hops,
        "max_hops": max_hops,
        "filtering": filtering,
        "aggregation": aggregation,
        "grouping": grouping,
        "ordering": ordering,
        "with_stage": with_stage,
        "variable_length": variable_length,
        "temporal": temporal,
        "distinct": distinct,
        "comparison": comparison,
        "set_op": set_op,
        "subquery": subquery,
    }


def _family(family: str, difficulty_band: list[str], structural_markers: dict, display_name: str, description: str) -> dict:
    return {
        "family": family,
        "difficulty_band": difficulty_band,
        "structural_markers": structural_markers,
        "display_name": display_name,
        "description": description,
    }


QUERY_TYPE_REGISTRY = {
    "LOOKUP": [
        _family(
            "lookup_node_return",
            ["L1"],
            _markers(),
            "Node lookup",
            "Return a matched node or a direct projection of its fields.",
        ),
        _family(
            "lookup_property_projection",
            ["L1"],
            _markers(),
            "Property lookup",
            "Return one or more properties from a matched node.",
        ),
        _family(
            "lookup_entity_detail",
            ["L1", "L2"],
            _markers(filtering=True),
            "Entity detail lookup",
            "Return a compact entity detail view with light filtering.",
        ),
    ],
    "FILTER": [
        _family(
            "filter_single_condition",
            ["L2"],
            _markers(filtering=True),
            "Single condition filter",
            "Filter by one property condition.",
        ),
        _family(
            "filter_boolean_combo",
            ["L2", "L3", "L4"],
            _markers(filtering=True),
            "Boolean combo filter",
            "Filter with AND/OR combinations.",
        ),
        _family(
            "filter_range_condition",
            ["L2", "L3"],
            _markers(filtering=True, temporal=True),
            "Range filter",
            "Filter by a numeric or temporal range.",
        ),
    ],
    "SORT_TOPK": [
        _family(
            "sort_single_metric",
            ["L3", "L4"],
            _markers(ordering=True),
            "Sorted metric projection",
            "Sort by one metric and project ranked results.",
        ),
        _family(
            "topk_entities",
            ["L4"],
            _markers(ordering=True),
            "Top-K entities",
            "Return the top-K entities by a score or count.",
        ),
        _family(
            "sorted_filtered_projection",
            ["L4"],
            _markers(filtering=True, ordering=True),
            "Sorted filtered projection",
            "Combine filtering with ordering and limit.",
        ),
    ],
    "AGGREGATION": [
        _family(
            "aggregate_global_count",
            ["L4"],
            _markers(aggregation=True),
            "Global count",
            "Aggregate over the entire matched set.",
        ),
        _family(
            "aggregate_filtered_count",
            ["L4"],
            _markers(filtering=True, aggregation=True),
            "Filtered count",
            "Aggregate after applying a filter.",
        ),
        _family(
            "aggregate_scalar_metric",
            ["L4"],
            _markers(aggregation=True),
            "Scalar aggregate",
            "Return a scalar aggregate such as sum or average.",
        ),
    ],
    "GROUP_AGG": [
        _family(
            "group_count",
            ["L4"],
            _markers(aggregation=True, grouping=True),
            "Grouped count",
            "Group by a field and count members.",
        ),
        _family(
            "group_ranked_count",
            ["L4", "L5"],
            _markers(aggregation=True, grouping=True, ordering=True),
            "Ranked grouped count",
            "Group by a field and rank the groups.",
        ),
        _family(
            "group_filtered_aggregate",
            ["L5", "L6"],
            _markers(filtering=True, aggregation=True, grouping=True),
            "Filtered grouped aggregate",
            "Filter before grouping and aggregating.",
        ),
    ],
    "MULTI_HOP": [
        _family(
            "two_hop_return",
            ["L5"],
            _markers(min_hops=2, max_hops=2),
            "Two-hop return",
            "Return results from a two-hop traversal.",
        ),
        _family(
            "two_hop_filtered",
            ["L5", "L6"],
            _markers(min_hops=2, max_hops=2, filtering=True),
            "Two-hop filtered",
            "Traverse two hops with an intermediate filter.",
        ),
        _family(
            "multi_hop_projection",
            ["L6", "L7"],
            _markers(min_hops=2, max_hops=3),
            "Multi-hop projection",
            "Project selected fields after a multi-hop traversal.",
        ),
    ],
    "COMPARISON": [
        _family(
            "attribute_comparison",
            ["L3", "L4"],
            _markers(comparison=True),
            "Attribute comparison",
            "Compare one attribute against another value or attribute.",
        ),
        _family(
            "aggregate_comparison",
            ["L4", "L5"],
            _markers(comparison=True, aggregation=True),
            "Aggregate comparison",
            "Compare aggregated values across groups or nodes.",
        ),
        _family(
            "rank_position_comparison",
            ["L4", "L5"],
            _markers(comparison=True, ordering=True),
            "Rank comparison",
            "Compare items by position or rank in a result set.",
        ),
    ],
    "TEMPORAL": [
        _family(
            "time_range_filter",
            ["L2", "L3"],
            _markers(filtering=True, temporal=True),
            "Time range filter",
            "Filter by a temporal range.",
        ),
        _family(
            "recent_or_earliest",
            ["L3", "L4"],
            _markers(filtering=True, temporal=True, ordering=True),
            "Recent or earliest",
            "Select the most recent or earliest records.",
        ),
        _family(
            "temporal_ordering",
            ["L4"],
            _markers(temporal=True, ordering=True),
            "Temporal ordering",
            "Order results by a time field.",
        ),
    ],
    "PATH": [
        _family(
            "path_existence",
            ["L4", "L5"],
            _markers(min_hops=1, max_hops=2),
            "Path existence",
            "Check whether a path exists between entities.",
        ),
        _family(
            "variable_length_path",
            ["L5", "L6"],
            _markers(min_hops=1, variable_length=True),
            "Variable length path",
            "Use a variable-length traversal pattern.",
        ),
        _family(
            "path_constrained_target",
            ["L6", "L7"],
            _markers(min_hops=2, max_hops=3, filtering=True),
            "Constrained path target",
            "Constrain the endpoint of a path traversal.",
        ),
    ],
    "SET_OP": [
        _family(
            "distinct_projection",
            ["L2", "L3"],
            _markers(distinct=True, set_op=True),
            "Distinct projection",
            "Return deduplicated values.",
        ),
        _family(
            "set_like_union_projection",
            ["L4", "L5"],
            _markers(set_op=True),
            "Union-like projection",
            "Combine result sets in a set-like way.",
        ),
        _family(
            "membership_intersection_style",
            ["L4", "L5"],
            _markers(set_op=True, filtering=True),
            "Intersection style",
            "Select items satisfying overlapping membership constraints.",
        ),
    ],
    "SUBQUERY": [
        _family(
            "with_stage_filter",
            ["L7"],
            _markers(with_stage=True, subquery=True, filtering=True),
            "WITH stage filter",
            "Use WITH to stage a filtered sub-result.",
        ),
        _family(
            "with_stage_aggregate",
            ["L7", "L8"],
            _markers(with_stage=True, subquery=True, aggregation=True),
            "WITH stage aggregate",
            "Use WITH to stage and aggregate intermediate results.",
        ),
        _family(
            "two_stage_refine",
            ["L7"],
            _markers(with_stage=True, subquery=True, filtering=True),
            "Two-stage refinement",
            "Refine results through two dependent query stages.",
        ),
    ],
    "HYBRID": [
        _family(
            "temporal_aggregate_hybrid",
            ["L6", "L7"],
            _markers(filtering=True, aggregation=True, temporal=True),
            "Temporal aggregate hybrid",
            "Combine temporal filtering with aggregation.",
        ),
        _family(
            "path_aggregate_hybrid",
            ["L7", "L8"],
            _markers(min_hops=2, aggregation=True, variable_length=True),
            "Path aggregate hybrid",
            "Combine path traversal with aggregation.",
        ),
        _family(
            "comparison_subquery_hybrid",
            ["L7", "L8"],
            _markers(comparison=True, with_stage=True, subquery=True),
            "Comparison subquery hybrid",
            "Combine comparison logic with staged subqueries.",
        ),
    ],
}

