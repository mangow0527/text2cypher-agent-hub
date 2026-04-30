from __future__ import annotations

import json
from typing import Any, Dict

from .models import IssueTicket


def _dedupe_lines(text: str) -> str:
    seen: set[str] = set()
    compacted: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        normalized = line.strip()
        if normalized and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        if line or (compacted and compacted[-1] != ""):
            compacted.append(line)
    while compacted and compacted[-1] == "":
        compacted.pop()
    return "\n".join(compacted)


def _compact_prompt_snapshot(prompt_snapshot: str, max_chars: int = 1200) -> str:
    compacted = _dedupe_lines(prompt_snapshot.strip())
    if len(compacted) <= max_chars:
        return compacted
    marker = "\n...[prompt truncated]...\n"
    head_budget = max_chars // 2
    tail_budget = max_chars - head_budget - len(marker)
    return compacted[:head_budget].rstrip() + marker + compacted[-tail_budget:].lstrip()


def _trim_text(value: str | None, max_chars: int) -> str | None:
    if not value:
        return value
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _compact_json_value(value: Any, max_chars: int = 600) -> Any:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return value
    if isinstance(value, list):
        compacted = value[:1]
    elif isinstance(value, dict):
        compacted = {key: value[key] for key in list(value)[:6]}
    else:
        compacted = str(value)
    serialized = json.dumps(compacted, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return compacted
    return _trim_text(serialized, max_chars)


def _build_repair_ticket_payload(ticket: IssueTicket) -> dict[str, Any]:
    execution = ticket.actual.execution
    execution_payload = None
    if execution is not None:
        execution_payload = {
            "success": execution.success,
            "row_count": execution.row_count,
            "error_message": _trim_text(execution.error_message, 240),
            "elapsed_ms": execution.elapsed_ms,
        }
    return {
        "ticket_id": ticket.ticket_id,
        "id": ticket.id,
        "difficulty": ticket.difficulty,
        "question": ticket.question,
        "expected": {"cypher": ticket.expected.cypher},
        "actual": {
            "generated_cypher": ticket.actual.generated_cypher,
            "execution": execution_payload,
        },
        "evaluation": {
            "verdict": ticket.evaluation.verdict,
            "primary_metrics": ticket.evaluation.primary_metrics.model_dump(mode="json"),
            "secondary_signals": ticket.evaluation.secondary_signals.model_dump(mode="json"),
        },
    }


def _build_repair_ticket_payload_from_context(context: Dict[str, Any]) -> dict[str, Any]:
    evaluation_summary = context.get("evaluation_summary") or {}
    return {
        "ticket_id": context.get("ticket_id") or "diagnosis-context",
        "id": context.get("id") or "unknown",
        "difficulty": context.get("difficulty") or "L1",
        "question": context.get("question") or "",
        "expected": {"cypher": (context.get("sql_pair") or {}).get("expected_cypher", "")},
        "actual": {"generated_cypher": (context.get("sql_pair") or {}).get("actual_cypher", "")},
        "evaluation": {
            "verdict": evaluation_summary.get("verdict") or "fail",
            "primary_metrics": evaluation_summary.get("primary_metrics") or {},
            "secondary_signals": evaluation_summary.get("secondary_signals") or {},
        },
    }


def _compact_relevant_prompt_fragments(fragments: Dict[str, Any]) -> Dict[str, Any]:
    compacted: Dict[str, Any] = {}
    seen_lines: set[str] = set()
    for key, value in fragments.items():
        if isinstance(value, str):
            unique_lines: list[str] = []
            for raw_line in value.splitlines():
                line = raw_line.strip()
                if not line or line in seen_lines:
                    continue
                seen_lines.add(line)
                unique_lines.append(raw_line)
            compacted[key] = _compact_prompt_snapshot("\n".join(unique_lines), max_chars=320)
        else:
            compacted[key] = value
    return compacted


def _compact_recent_repairs(repairs: Any) -> list[dict[str, Any]]:
    if not isinstance(repairs, list):
        return []
    compacted: list[dict[str, Any]] = []
    for repair in repairs[:2]:
        if not isinstance(repair, dict):
            continue
        compacted.append(
            {
                "knowledge_type": repair.get("knowledge_type"),
                "suggestion": _trim_text(str(repair.get("suggestion") or ""), 180),
            }
        )
    return compacted


def _compact_prompt_evidence_for_context(context: Dict[str, Any]) -> str:
    prompt_evidence = str(context.get("prompt_evidence") or "")
    fragments = context.get("relevant_prompt_fragments") or {}
    fragment_lines: set[str] = set()
    if isinstance(fragments, dict):
        for value in fragments.values():
            if not isinstance(value, str):
                continue
            fragment_lines.update(line.strip() for line in value.splitlines() if line.strip())
    if not fragment_lines:
        return _compact_prompt_snapshot(prompt_evidence, max_chars=1200)
    filtered_lines = [
        raw_line
        for raw_line in prompt_evidence.splitlines()
        if raw_line.strip() and raw_line.strip() not in fragment_lines
    ]
    return _compact_prompt_snapshot("\n".join(filtered_lines), max_chars=1200)


def compact_diagnosis_context(context: Dict[str, Any]) -> Dict[str, Any]:
    generation_evidence = dict(context.get("generation_evidence") or {})
    generation_evidence.pop("input_prompt_snapshot", None)
    return {
        "question": context.get("question"),
        "difficulty": context.get("difficulty"),
        "sql_pair": context.get("sql_pair"),
        "evaluation_summary": context.get("evaluation_summary"),
        "failure_diff": context.get("failure_diff"),
        "prompt_evidence": _compact_prompt_evidence_for_context(context),
        "generation_evidence": _compact_json_value(generation_evidence, max_chars=900),
        "relevant_prompt_fragments": _compact_relevant_prompt_fragments(context.get("relevant_prompt_fragments", {})),
        "recent_applied_repairs": _compact_recent_repairs(context.get("recent_applied_repairs")),
    }


def build_repair_diagnosis_prompt(context: Dict[str, Any], *, ticket: IssueTicket | None = None) -> tuple[str, str]:
    compact_context = compact_diagnosis_context(context)
    compact_ticket = _build_repair_ticket_payload(ticket) if ticket is not None else _build_repair_ticket_payload_from_context(context)
    system_prompt = (
        "你是 Text2Cypher 系统中的知识修复诊断器。"
        "你的任务不是修 query，而是判断 knowledge-agent 应该补哪类知识。"
        "你只能使用这些知识类型：cypher_syntax, few_shot, system_prompt, business_knowledge。"
        "只返回 JSON，不要返回 Markdown、解释性正文或额外字段。"
        "JSON 必须包含这些字段：repairable, non_repairable_reason, primary_knowledge_type, secondary_knowledge_types, confidence, suggestion, rationale。"
        "suggestion、rationale、non_repairable_reason 必须使用中文，不能使用英文修复建议或英文原因说明；可以保留 label、relation、Cypher、ID 等原始英文术语。"
    )
    user_prompt = (
        f"IssueTicketSummary: {json.dumps(compact_ticket, ensure_ascii=False)}\n"
        f"DiagnosisContext: {json.dumps(compact_context, ensure_ascii=False)}\n"
        "诊断顺序：\n"
        "1. 先看 DiagnosisContext.generation_evidence：如果 generation_status=generation_failed，优先根据 failure_reason、generation_failure_reasons、generation_retry_count、last_llm_raw_output 判断失败发生在输出协议、Cypher 语法还是知识缺失。\n"
        "2. 再看 evaluation.primary_metrics.grammar：如果 grammar.score=0 或 parser_error 非空，优先考虑 cypher_syntax。\n"
        "3. 再看 IssueTicketSummary.actual.execution：如果 execution.success=false 或 error_message 非空，结合错误判断是 cypher_syntax 还是 business_knowledge。\n"
        "4. 再看 execution_accuracy.strict_check 和 semantic_check：如果语法和执行都成功但 strict/semantic 失败，说明 query 语义不等价；比较 expected.cypher 与 actual.generated_cypher，找缺失的 label、relation、path、filter、return shape。\n"
        "5. 最后看 DiagnosisContext.prompt_evidence：判断 expected 需要的 schema/path/business term/输出约束在 prompt snapshot 里是否存在，以及 actual 是否正确使用了这些知识。\n"
        "字段说明：\n"
        "- IssueTicketSummary.expected.cypher 是标准答案 query。\n"
        "- IssueTicketSummary.actual.generated_cypher 是本次提交的 query；如果 generation_failed，则是 testing-agent 派生出的候选文本。\n"
        "- evaluation.primary_metrics.grammar 是语法检查结果；parser_error 是语法失败的直接证据。\n"
        "- IssueTicketSummary.actual.execution 是实际执行结果；error_message 是执行失败的直接证据。\n"
        "- execution_accuracy.strict_check 是结果集严格比较；semantic_check 是语义等价判断。\n"
        "- secondary_signals.gleu 和 jaro_winkler_similarity 只能作为相似度辅助信号，不能单独决定根因。\n"
        "- DiagnosisContext.generation_evidence 记录生成过程证据，用来判断失败发生在生成、解析、重试还是评估阶段。\n"
        "- DiagnosisContext.failure_diff 是 repair-agent 用确定性代码生成的失败症状摘要，不等同于最终根因。\n"
        "- DiagnosisContext.prompt_evidence 是 cypher-generator-agent 当次使用的 prompt snapshot，已压缩用于诊断。\n"
        "- DiagnosisContext.relevant_prompt_fragments 是辅助提取片段；不能脱离 prompt_evidence 单独作为判断依据。\n"
        "- 你只诊断 knowledge-agent 知识缺口，不诊断 cypher-generator-agent 固定协议、parser、preflight 或 testing-agent evaluator 的 bug。\n"
        "知识类型选择规则：\n"
        "- cypher_syntax：Cypher 语法、括号、非法 clause、写操作、unsupported call、unsupported start clause 等问题。\n"
        "- few_shot：prompt_evidence 中已经有相关规则或业务知识，但缺少相似示例，导致模型没有把规则迁移到当前问题。\n"
        "- system_prompt：输出格式、只输出 Cypher、禁止解释、禁止 Markdown、固定协议、边界约束不清。\n"
        "- business_knowledge：schema、label、relation、path、filter、业务术语映射、实体关系知识缺失或错误。\n"
        "判断 prompt_evidence 的规则：\n"
        "- 如果 expected.cypher 需要的 schema/path/business term 在 prompt_evidence 中不存在，倾向 business_knowledge。\n"
        "- 如果 prompt_evidence 中已有必要规则，但 actual.generated_cypher 没有正确套用，倾向 few_shot。\n"
        "- 如果失败主要来自输出包装、解释文本、Markdown、非 JSON/非 Cypher 边界不清，倾向 system_prompt。\n"
        "- 如果失败主要来自 Cypher 结构本身不合法，倾向 cypher_syntax。\n"
        "输出 JSON schema：{\"repairable\": boolean, \"non_repairable_reason\": string, \"primary_knowledge_type\": string, \"secondary_knowledge_types\": string[], \"confidence\": number, \"suggestion\": string, \"rationale\": string}。\n"
        "输出语言要求：suggestion、rationale、non_repairable_reason 必须使用中文，不能使用英文修复建议或英文原因说明；可以保留 label、relation、Cypher、ID 等原始英文术语。\n"
        "如果 repairable=false，non_repairable_reason 必须说明为什么这不是 knowledge-agent 知识包缺口。\n"
        "primary_knowledge_type 和 secondary_knowledge_types 只能使用这些值：cypher_syntax, few_shot, system_prompt, business_knowledge。"
    )
    return system_prompt, user_prompt
