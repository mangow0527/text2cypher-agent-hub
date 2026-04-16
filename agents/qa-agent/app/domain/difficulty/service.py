from __future__ import annotations

import re


class DifficultyService:
    """Classify Cypher queries into deterministic L1-L8 structural buckets."""

    _AGGREGATION_PATTERN = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX|COLLECT)\s*\(", re.IGNORECASE)
    _RELATIONSHIP_PATTERN = re.compile(r"-\s*\[[^\]]*\]\s*(?:->|-\s*|<-\s*)")
    _BRACE_VARIABLE_PATH_PATTERN = re.compile(r"-\s*\[[^\]]*\]\s*->\s*\{\s*\d+\s*,\s*\d*\s*\}")

    def classify(self, cypher: str) -> str:
        text = self._normalize(cypher)

        hop_count = self._hop_count(text)
        has_where = " WHERE " in text
        has_order = " ORDER BY " in text
        has_agg = self._has_aggregation(text)
        has_with = " WITH " in text
        match_count = len(re.findall(r"\bMATCH\b", text))
        has_match_after_with = bool(re.search(r"\bWITH\b.*\bMATCH\b", text))
        aggregation_count = self._aggregation_count(text)
        has_nested_agg = has_with and aggregation_count >= 2 and (match_count > 1 or has_match_after_with)
        multi_stage = has_with and match_count > 1
        has_variable_length = self._has_variable_length(text)

        if has_variable_length:
            hop_count = max(hop_count, 2)

        if has_nested_agg:
            return "L8"
        if hop_count >= 3:
            return "L7"
        if multi_stage:
            return "L7"
        if hop_count >= 2 and (has_where or has_order or has_agg):
            return "L6"
        if hop_count >= 2:
            return "L5"
        if hop_count == 1 and (has_where or has_order or has_agg):
            return "L4"
        if hop_count == 1:
            return "L3"
        if has_agg or has_order:
            return "L4"
        if has_where:
            return "L2"
        return "L1"

    def _normalize(self, cypher: str) -> str:
        sanitized = self._sanitize(cypher)
        return " ".join(sanitized.strip().split()).upper()

    def _sanitize(self, cypher: str) -> str:
        result: list[str] = []
        index = 0
        length = len(cypher)
        in_single_quote = False
        in_double_quote = False
        in_line_comment = False
        in_block_comment = False

        while index < length:
            ch = cypher[index]
            nxt = cypher[index + 1] if index + 1 < length else ""

            if in_line_comment:
                if ch in "\r\n":
                    in_line_comment = False
                    result.append(ch)
                else:
                    result.append(" ")
            elif in_block_comment:
                if ch == "*" and nxt == "/":
                    result.extend([" ", " "])
                    index += 1
                    in_block_comment = False
                else:
                    result.append(ch if ch in "\r\n" else " ")
            elif in_single_quote:
                if ch == "\\" and nxt:
                    result.extend([" ", " "])
                    index += 1
                elif ch == "'":
                    in_single_quote = False
                    result.append(" ")
                else:
                    result.append(" ")
            elif in_double_quote:
                if ch == "\\" and nxt:
                    result.extend([" ", " "])
                    index += 1
                elif ch == '"':
                    in_double_quote = False
                    result.append(" ")
                else:
                    result.append(" ")
            else:
                if ch == "'" :
                    in_single_quote = True
                    result.append(" ")
                elif ch == '"':
                    in_double_quote = True
                    result.append(" ")
                elif ch == "/" and nxt == "/":
                    in_line_comment = True
                    result.extend([" ", " "])
                    index += 1
                elif ch == "/" and nxt == "*":
                    in_block_comment = True
                    result.extend([" ", " "])
                    index += 1
                else:
                    result.append(ch)

            index += 1

        return "".join(result)

    def _has_aggregation(self, text: str) -> bool:
        return bool(self._AGGREGATION_PATTERN.search(text))

    def _aggregation_count(self, text: str) -> int:
        return len(self._AGGREGATION_PATTERN.findall(text))

    def _hop_count(self, text: str) -> int:
        count = 0
        for relationship in self._RELATIONSHIP_PATTERN.findall(text):
            count += self._relationship_weight(relationship)
        return count

    def _relationship_weight(self, relationship: str) -> int:
        variable_length = re.search(r"\*\s*(\d+)?\s*(?:\.\.\s*(\d+)?)?", relationship)
        if not variable_length:
            return 1

        return 2

    def _has_variable_length(self, text: str) -> bool:
        if self._BRACE_VARIABLE_PATH_PATTERN.search(text):
            return True
        for relationship in self._RELATIONSHIP_PATTERN.findall(text):
            if "*" in relationship:
                return True
        return False
