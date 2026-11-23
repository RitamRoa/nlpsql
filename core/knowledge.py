import re
import json
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Any
from .adapters.base import DBAdapter, Schema
from .semantic_layer import SemanticLayer


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
    semantic_definitions: list[str] = field(default_factory=list)
    fuzzy_matched_values: list[tuple[str, str, str]] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        lines = []
        if self.matched_values:
            lines.append("-- Database Values Detected:")
            for tbl, col, val in self.matched_values:
                lines.append(
                    f"--   {tbl}.{col} contains literal {json.dumps(val, ensure_ascii=True)}"
                )
        if self.fuzzy_matched_values:
            lines.append("-- Fuzzy-Matched Database Values (verify the intended value):")
            for tbl, col, val in self.fuzzy_matched_values:
                lines.append(
                    f"--   {tbl}.{col} likely matches literal {json.dumps(val, ensure_ascii=True)}"
                )
        if self.matched_rules:
            lines.append("-- Relevant Business Rules:")
            for rule in self.matched_rules:
                lines.append(
                    "--   "
                    f"name={json.dumps(rule.name, ensure_ascii=True)}; "
                    f"description={json.dumps(rule.description, ensure_ascii=True)}; "
                    f"filter={json.dumps(rule.sql_expression, ensure_ascii=True)}"
                )
        if self.semantic_definitions:
            lines.append("-- Relevant Semantic Definitions (database documentation):")
            lines.extend(
                f"--   {json.dumps(definition, ensure_ascii=True)}"
                for definition in self.semantic_definitions
            )
        return "\n".join(lines)


class DomainKnowledgeEngine:
    """Extracts distinct column values and registers domain business rules

    to disambiguate user queries.
    """

    def __init__(
        self,
        adapter: DBAdapter,
        max_distinct_per_col: int = 50,
        semantic_layer: SemanticLayer | None = None,
    ):
        self.adapter = adapter
        self.max_distinct_per_col = max_distinct_per_col
        self.semantic_layer = semantic_layer or SemanticLayer()
        self.value_index: list[tuple[str, str, str]] = []  # (table, col, val)
        self.rules: list[tuple[set[str], BusinessRule]] = []
        for rule in self.semantic_layer.business_rules:
            self.register_rule(
                keywords=rule.keywords,
                name=rule.name,
                description=rule.description,
                sql_expression=rule.sql_expression,
            )
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
        allowed_schema: Schema | None = None,
    ) -> KnowledgeContext:
        """Finds database values and business rules mentioned in the user question."""
        q_lower = question.lower()
        q_tokens = set(re.findall(r"[a-zA-Z0-9]+", q_lower))

        matched_values = []
        fuzzy_matched_values = []
        value_candidates = []
        allowed_columns = (
            {
                table.name.lower(): {column.name.lower() for column in table.columns}
                for table in allowed_schema.tables
            }
            if allowed_schema is not None
            else None
        )
        visible_tables = {
            table.name.lower(): {column.name.lower() for column in table.columns}
            for table in (allowed_schema.tables if allowed_schema is not None else [])
        }
        all_schema_columns = (
            {
                table.name.lower(): {column.name.lower() for column in table.columns}
                for table in self.adapter.get_schema().tables
            }
            if allowed_schema is not None
            else {}
        )

        def expression_is_visible(tree) -> bool:
            if allowed_schema is None:
                return True
            from sqlglot import exp

            for table in tree.find_all(exp.Table):
                if table.name.lower() not in visible_tables:
                    return False
            for column in tree.find_all(exp.Column):
                if column.name == "*":
                    continue
                if column.table:
                    if column.name.lower() not in visible_tables.get(column.table.lower(), set()):
                        return False
                else:
                    full_owners = {
                        table_name for table_name, columns in all_schema_columns.items()
                        if column.name.lower() in columns
                    }
                    visible_owners = {
                        table_name for table_name, columns in visible_tables.items()
                        if column.name.lower() in columns
                    }
                    if not full_owners or not full_owners.issubset(visible_owners):
                        return False
            return True
        for tbl, col, val in self.value_index:
            if allowed_tables and tbl.lower() not in {name.lower() for name in allowed_tables}:
                continue
            if allowed_columns is not None and col.lower() not in allowed_columns.get(tbl.lower(), set()):
                continue
            val_lower = val.lower()
            if val_lower in q_lower or val_lower in q_tokens:
                matched_values.append((tbl, col, val))
            else:
                value_candidates.append((tbl, col, val))

        used_columns: set[tuple[str, str]] = set()
        for token in q_tokens:
            matches = []
            for tbl, col, value in value_candidates:
                normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
                ratio = SequenceMatcher(None, token, normalized).ratio()
                if len(token) >= 4 and ratio >= 0.84:
                    matches.append((ratio, tbl, col, value))
                elif len(token) >= 3 and normalized.startswith(token) and len(normalized) > len(token):
                    matches.append((0.84, tbl, col, value))
            if not matches:
                continue
            matches.sort(reverse=True)
            best_ratio = matches[0][0]
            best = [item for item in matches if item[0] == best_ratio]
            unique_values = {(item[1], item[2], item[3]) for item in best}
            if len(unique_values) == 1:
                _, tbl, col, value = best[0]
                key = (tbl.lower(), col.lower())
                if key not in used_columns and (tbl, col, value) not in matched_values:
                    fuzzy_matched_values.append((tbl, col, value))
                    used_columns.add(key)

        matched_rules = []
        for kw_set, rule in self.rules:
            if kw_set & q_tokens or any(kw in q_lower for kw in kw_set):
                if allowed_schema is not None:
                    import sqlglot

                    try:
                        tree = sqlglot.parse_one(
                            f"SELECT 1 WHERE {rule.sql_expression}",
                            read=self.adapter.dialect(),
                        )
                    except sqlglot.errors.ParseError:
                        continue
                    if not expression_is_visible(tree):
                        continue
                matched_rules.append(rule)

        semantic_definitions = []
        for table_name, table_definition in self.semantic_layer.tables.items():
            if allowed_tables and table_name.lower() not in {
                name.lower() for name in allowed_tables
            }:
                continue
            table_tokens = set(re.findall(r"[a-zA-Z0-9]+", table_name.lower()))
            table_synonyms = {
                token
                for synonym in table_definition.synonyms
                for token in re.findall(r"[a-zA-Z0-9]+", synonym.lower())
            }
            if (table_tokens | table_synonyms) & q_tokens and table_definition.description:
                semantic_definitions.append(
                    f"Table {table_name}: {table_definition.description}"
                )
            for column_name, column_definition in table_definition.columns.items():
                if allowed_columns is not None and column_name.lower() not in allowed_columns.get(table_name.lower(), set()):
                    continue
                column_tokens = set(re.findall(r"[a-zA-Z0-9]+", column_name.lower()))
                column_synonyms = {
                    token
                    for synonym in column_definition.synonyms
                    for token in re.findall(r"[a-zA-Z0-9]+", synonym.lower())
                }
                if (
                    (column_tokens | column_synonyms) & q_tokens
                    and column_definition.description
                ):
                    semantic_definitions.append(
                        f"Column {table_name}.{column_name}: {column_definition.description}"
                    )

        normalized_question = " " + " ".join(
            re.findall(r"[a-zA-Z0-9]+", q_lower)
        ) + " "
        for metric in self.semantic_layer.metrics:
            phrases = [metric.name.replace("_", " "), *metric.synonyms]
            if not any(
                " " + " ".join(re.findall(r"[a-zA-Z0-9]+", phrase.lower())) + " "
                in normalized_question
                for phrase in phrases
            ):
                continue
            if allowed_schema is not None:
                import sqlglot

                try:
                    metric_tree = sqlglot.parse_one(
                        f"SELECT {metric.expression}",
                        read=self.adapter.dialect(),
                    )
                except sqlglot.errors.ParseError:
                    continue
                if not expression_is_visible(metric_tree):
                    continue
            semantic_definitions.append(
                f"Metric {metric.name}: {metric.description}; "
                f"definition: {metric.expression}"
            )

        return KnowledgeContext(
            matched_values=matched_values,
            matched_rules=matched_rules,
            semantic_definitions=semantic_definitions,
            fuzzy_matched_values=fuzzy_matched_values,
        )
