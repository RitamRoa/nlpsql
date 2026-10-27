import re
from dataclasses import dataclass
from ..adapters.base import Result


@dataclass
class SemanticReview:
    is_acceptable: bool
    feedback: str | None = None


class SemanticCritic:
    """Inspects query syntax and executed results for semantic/logical flaws

    such as empty results caused by case mismatch or Cartesian cross-joins.
    """

    def review(
        self,
        question: str,
        sql: str,
        result: Result,
        dialect: str = "sqlite",
    ) -> SemanticReview:
        # Check 1: Empty results on an explicit entity lookup
        if len(result.rows) == 0:
            # Check if query uses an exact '=' match on a string literal that could have case issues
            match = re.search(r"=\s*'([^']+)'", sql)
            if match:
                lit = match.group(1)
                return SemanticReview(
                    is_acceptable=False,
                    feedback=(
                        f"Query executed successfully but returned 0 rows. "
                        f"The exact match '{lit}' may have failed due to case sensitivity. "
                        f"Try matching with LOWER(...) or LIKE '%{lit}%'."
                    ),
                )

        # Check 2: Potential unintended Cartesian product (e.g. FROM A, B without WHERE or JOIN ON)
        sql_lower = sql.lower()
        if (
            "," in sql_lower.split("where")[0]
            and "join" not in sql_lower
            and "where" not in sql_lower
        ):
            if "from" in sql_lower:
                from_part = sql_lower.split("from")[1].split("where")[0]
                if "," in from_part:
                    return SemanticReview(
                        is_acceptable=False,
                        feedback="Query performs a comma Cartesian join without an explicit JOIN ON or WHERE join condition.",
                    )

        return SemanticReview(is_acceptable=True, feedback=None)
