import re
from typing import Protocol
from .adapters.base import Schema, Table, Column


class BaseRetriever(Protocol):
    def retrieve(self, question: str, schema: Schema) -> Schema: ...


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase words, splitting underscores and camelCase."""
    # Split camelCase and underscores
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[a-zA-Z0-9]+", s1.lower())
    return {w for w in words if len(w) > 1}


class SchemaRetriever:
    """Two-stage schema retrieval and pruning:

    1. Ranks and selects relevant tables based on lexical/semantic overlap.
    2. Preserves foreign key relationships so valid JOIN queries can be formed.
    3. Prunes irrelevant columns while always preserving primary and foreign keys.
    """

    def __init__(
        self,
        max_tables: int = 4,
        max_cols_per_table: int = 25,
        include_foreign_key_paths: bool = True,
    ):
        self.max_tables = max_tables
        self.max_cols_per_table = max_cols_per_table
        self.include_foreign_key_paths = include_foreign_key_paths

    def _score_table(self, question_tokens: set[str], table: Table) -> float:
        """Scores a table based on token overlap with table name and column names."""
        tbl_tokens = _tokenize(table.name)
        # Give higher weight to matches with the table name itself
        tbl_match_score = len(question_tokens & tbl_tokens) * 3.0

        col_tokens = set()
        for col in table.columns:
            col_tokens.update(_tokenize(col.name))

        col_match_score = len(question_tokens & col_tokens) * 1.0
        return tbl_match_score + col_match_score

    def _score_column(
        self, question_tokens: set[str], column: Column, is_key: bool
    ) -> float:
        """Scores a column.

        Keys receive a base score to avoid getting pruned if relevant.
        """
        col_tokens = _tokenize(column.name)
        match_score = len(question_tokens & col_tokens) * 2.0
        if is_key:
            match_score += 1.5
        return match_score

    def retrieve(self, question: str, schema: Schema) -> Schema:
        """Prune a full database schema to only tables and columns relevant to the question."""
        if len(schema.tables) <= self.max_tables:
            # If schema is already small, return as-is
            return schema

        q_tokens = _tokenize(question)

        # 1. Score and rank all tables
        scored_tables = []
        table_map: dict[str, Table] = {}
        for t in schema.tables:
            table_map[t.name.lower()] = t
            score = self._score_table(q_tokens, t)
            scored_tables.append((score, t))

        # Sort descending by score
        scored_tables.sort(key=lambda x: x[0], reverse=True)

        # Select top tables with positive score (or at least top 1)
        selected_names = set()
        for score, t in scored_tables:
            if score > 0 and len(selected_names) < self.max_tables:
                selected_names.add(t.name.lower())

        if not selected_names and scored_tables:
            # Fallback to top-scoring table
            selected_names.add(scored_tables[0][1].name.lower())

        # 2. Expand with Foreign Key linked tables if requested
        if self.include_foreign_key_paths:
            for t in list(schema.tables):
                t_lower = t.name.lower()
                if t_lower in selected_names:
                    for col, ref_table, ref_col in t.foreign_keys:
                        ref_lower = ref_table.lower()
                        if (
                            ref_lower in table_map
                            and len(selected_names) < self.max_tables + 2
                        ):
                            selected_names.add(ref_lower)

        # 3. Column Pruning for selected tables
        pruned_tables = []
        for t_lower in selected_names:
            t = table_map[t_lower]
            # Identify essential key columns that must NEVER be pruned
            essential_cols = {c.name.lower() for c in t.columns if c.is_pk}
            for col, ref_table, ref_col in t.foreign_keys:
                essential_cols.add(col.lower())

            # Score each column
            scored_cols = []
            for col in t.columns:
                is_essential = col.name.lower() in essential_cols
                c_score = self._score_column(q_tokens, col, is_essential)
                scored_cols.append((c_score, is_essential, col))

            # Sort descending: essential first or highest scored
            scored_cols.sort(key=lambda x: (x[1], x[0]), reverse=True)

            # Keep top columns up to max_cols_per_table, but ensure all essential columns remain
            kept_columns: list[Column] = []
            for score, is_essential, col in scored_cols:
                if (
                    is_essential
                    or len(kept_columns) < self.max_cols_per_table
                    or score > 0
                ):
                    kept_columns.append(col)

            # Keep original column ordering
            kept_names = {c.name.lower() for c in kept_columns}
            ordered_columns = [
                c for c in t.columns if c.name.lower() in kept_names
            ]

            pruned_tables.append(
                Table(
                    name=t.name,
                    columns=ordered_columns,
                    foreign_keys=t.foreign_keys,
                )
            )

        return Schema(tables=pruned_tables)
