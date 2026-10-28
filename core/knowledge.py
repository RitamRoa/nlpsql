import re
from dataclasses import dataclass, field
from typing import Any
from .adapters.base import DBAdapter


@dataclass
class BusinessRule:
    name: str
    description: str
    sql_expression: str  # e.g., "salary >= 90000" or "amount > 1000"


@dataclass
class KnowledgeContext:
    matched_values: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (table, column, value)
    matched_rules: list[BusinessRule] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        lines = []
        if self.matched_values:
            lines.append("-- Database Values Detected:")
            for tbl, col, val in self.matched_values:
                lines.append(f"--   {tbl}.{col} contains literal '{val}'")
        if self.matched_rules:
            lines.append("-- Relevant Business Rules:")
            for rule in self.matched_rules:
                lines.append(
                    f"--   {rule.name}: {rule.description} -> filter with: {rule.sql_expression}"
                )
        return "\n".join(lines)


class DomainKnowledgeEngine:
    """Extracts distinct column values and registers domain business rules

    to disambiguate user queries.
    """

    def __init__(self, adapter: DBAdapter, max_distinct_per_col: int = 50):
        self.adapter = adapter
        self.max_distinct_per_col = max_distinct_per_col
        self.value_index: list[tuple[str, str, str]] = []  # (table, col, val)
        self.rules: list[tuple[set[str], BusinessRule]] = []
        self._build_value_index()

    def _build_value_index(self):
        """Scans low-cardinality text columns to index literal values."""
        schema = self.adapter.get_schema()
        for tbl in schema.tables:
            for col in tbl.columns:
                col_type = col.type.upper()
                if any(t in col_type for t in ("TEXT", "CHAR", "VARCHAR")):
                    try:
                        res = self.adapter.execute_readonly(
                            f"SELECT DISTINCT {col.name} FROM {tbl.name} WHERE {col.name} IS NOT NULL LIMIT {self.max_distinct_per_col};"
                        )
                        for row in res.rows:
                            if row and row[0] is not None:
                                val_str = str(row[0]).strip()
                                if val_str:
                                    self.value_index.append(
                                        (tbl.name, col.name, val_str)
                                    )
                    except Exception:
                        continue

    def register_rule(
        self,
        keywords: list[str],
        name: str,
        description: str,
        sql_expression: str,
    ):
        """Register a business rule mapped to trigger keywords."""
        kw_set = {k.lower() for k in keywords}
        self.rules.append(
            (kw_set, BusinessRule(name, description, sql_expression))
        )

    def resolve(
        self,
        question: str,
        allowed_tables: set[str] | list[str] | None = None,
    ) -> KnowledgeContext:
        """Finds database values and business rules mentioned in the user question."""
        q_lower = question.lower()
        q_tokens = set(re.findall(r"[a-zA-Z0-9]+", q_lower))

        matched_values = []
        for tbl, col, val in self.value_index:
            if allowed_tables and tbl not in allowed_tables:
                continue
            val_lower = val.lower()
            # Match exact substring or token match
            if val_lower in q_lower or val_lower in q_tokens:
                matched_values.append((tbl, col, val))

        matched_rules = []
        for kw_set, rule in self.rules:
            if kw_set & q_tokens or any(kw in q_lower for kw in kw_set):
                matched_rules.append(rule)

        return KnowledgeContext(
            matched_values=matched_values, matched_rules=matched_rules
        )
