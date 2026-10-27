from collections import Counter
from dataclasses import dataclass
from typing import Any
from core.adapters.base import Result


@dataclass
class EvalMetrics:
    total_examples: int = 0
    valid_sql_count: int = 0  # Parsed and executed without error
    exec_accuracy_count: int = 0  # Result matches gold query result
    repaired_success_count: int = 0  # Failed attempt 1, succeeded on repair
    avg_inference_time_s: float = 0.0
    avg_total_time_s: float = 0.0

    @property
    def valid_sql_rate(self) -> float:
        return (
            (self.valid_sql_count / self.total_examples * 100.0)
            if self.total_examples > 0
            else 0.0
        )

    @property
    def exec_accuracy(self) -> float:
        return (
            (self.exec_accuracy_count / self.total_examples * 100.0)
            if self.total_examples > 0
            else 0.0
        )

    @property
    def repair_recovery_rate(self) -> float:
        failed_first_attempt = self.total_examples - (
            self.valid_sql_count - self.repaired_success_count
        )
        if failed_first_attempt <= 0:
            return 0.0
        return self.repaired_success_count / failed_first_attempt * 100.0


def _normalize_cell(val: Any) -> Any:
    """Normalize a database cell value for robust comparison across engines."""
    if val is None:
        return None
    if isinstance(val, float):
        # Round floating points to 4 decimals to avoid float representation mismatch
        return round(val, 4)
    if isinstance(val, (int, bool)):
        return val
    # Standardize string representation
    s = str(val).strip()
    # Try converting numeric strings to floats if applicable
    try:
        f = float(s)
        return round(f, 4)
    except ValueError:
        return s.lower()


def _normalize_row(row: tuple | list) -> tuple:
    return tuple(_normalize_cell(v) for v in row)


def compare_results(
    pred_result: Result | None,
    gold_result: Result | None,
    order_sensitive: bool = False,
) -> bool:
    """Compares the output of a predicted query against the gold query.

    Handles column header invariance, row reordering, and type normalization.
    """
    if pred_result is None or gold_result is None:
        return False

    pred_rows = [_normalize_row(r) for r in pred_result.rows]
    gold_rows = [_normalize_row(r) for r in gold_result.rows]

    if len(pred_rows) != len(gold_rows):
        return False

    if order_sensitive:
        return pred_rows == gold_rows

    # Unordered multiset comparison (handles duplicate rows correctly)
    return Counter(pred_rows) == Counter(gold_rows)
