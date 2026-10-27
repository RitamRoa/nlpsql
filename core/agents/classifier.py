import re
from dataclasses import dataclass


@dataclass
class QueryClassification:
    complexity: str  # "EASY", "MEDIUM", "COMPLEX"
    requires_join: bool
    requires_aggregation: bool
    requires_subquery: bool


class QueryClassifier:
    """Classifies user natural-language questions to guide downstream SQL drafting."""

    AGG_KEYWORDS = {
        "count",
        "total",
        "sum",
        "average",
        "avg",
        "minimum",
        "min",
        "maximum",
        "max",
        "highest",
        "lowest",
        "most",
        "least",
        "how many",
    }
    JOIN_KEYWORDS = {
        "department of",
        "sales by",
        "manager of",
        "sales made by",
        "for each department",
        "in each department",
        "who made sales",
    }
    SUBQUERY_KEYWORDS = {
        "did not",
        "never",
        "no sales",
        "haven't",
        "above average",
        "more than average",
    }

    def classify(
        self, question: str, num_tables_linked: int = 1
    ) -> QueryClassification:
        q_lower = question.lower()

        has_agg = any(k in q_lower for k in self.AGG_KEYWORDS)
        has_join = (
            num_tables_linked > 1
            or any(k in q_lower for k in self.JOIN_KEYWORDS)
            or ("employee" in q_lower and "department" in q_lower)
            or ("employee" in q_lower and "sales" in q_lower)
        )
        has_subquery = any(k in q_lower for k in self.SUBQUERY_KEYWORDS)

        if has_subquery:
            complexity = "COMPLEX"
        elif has_join or has_agg:
            complexity = "MEDIUM"
        else:
            complexity = "EASY"

        return QueryClassification(
            complexity=complexity,
            requires_join=has_join,
            requires_aggregation=has_agg,
            requires_subquery=has_subquery,
        )
