"""Local, per-schema feedback storage for reviewed question/SQL examples."""
import os
import sqlite3
from pathlib import Path
from threading import Lock

from .few_shot import FewShotExample


class FeedbackStore:
    _lock = Lock()

    def __init__(self, path: str | None = None):
        if path:
            self.path = path
        else:
            app_data = os.environ.get("LOCALAPPDATA") or str(Path.home())
            directory = Path(app_data) / "NLPSQL"
            self.path = str(directory / "feedback.sqlite3")

    def _initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY,
                    schema_name TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
                    corrected INTEGER NOT NULL DEFAULT 0,
                    before_match INTEGER,
                    after_match INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(feedback)")
            }
            if "before_match" not in columns:
                connection.execute("ALTER TABLE feedback ADD COLUMN before_match INTEGER")
            if "after_match" not in columns:
                connection.execute("ALTER TABLE feedback ADD COLUMN after_match INTEGER")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS feedback_schema_created "
                "ON feedback(schema_name, created_at)"
            )

    def add(
        self,
        schema_name: str,
        question: str,
        sql: str,
        rating: int,
        corrected: bool = False,
        before_match: bool | None = None,
        after_match: bool | None = None,
    ) -> None:
        if rating not in {-1, 1}:
            raise ValueError("Feedback rating must be 1 (helpful) or -1 (not helpful).")
        if not question.strip() or not sql.strip():
            raise ValueError("Feedback requires a question and SQL.")
        self._initialize()
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """INSERT INTO feedback
                   (schema_name, question, sql, rating, corrected, before_match, after_match)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    schema_name, question.strip(), sql.strip(), rating, int(corrected),
                    None if before_match is None else int(before_match),
                    None if after_match is None else int(after_match),
                ),
            )

    def examples(self, schema_name: str, limit: int = 100) -> list[FewShotExample]:
        if not os.path.exists(self.path):
            return []
        with self._lock, sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """SELECT question, sql FROM feedback
                   WHERE schema_name = ? AND rating = 1
                   ORDER BY corrected DESC, created_at DESC LIMIT ?""",
                (schema_name, limit),
            ).fetchall()
        return [FewShotExample(question=row[0], sql=row[1]) for row in rows]

    def stats(self, schema_name: str) -> dict[str, int | float]:
        if not os.path.exists(self.path):
            return {
                "total": 0, "helpful": 0, "corrected": 0, "helpful_rate": 0.0,
                "measured": 0, "before_accuracy": 0.0, "after_accuracy": 0.0,
            }
        with self._lock, sqlite3.connect(self.path) as connection:
            total, positive, corrections, before_sum, before_count, after_sum, after_count = connection.execute(
                """SELECT COUNT(*), COALESCE(SUM(rating = 1), 0),
                          COALESCE(SUM(corrected), 0),
                          COALESCE(SUM(before_match), 0), COUNT(before_match),
                          COALESCE(SUM(after_match), 0), COUNT(after_match)
                   FROM feedback WHERE schema_name = ?""",
                (schema_name,),
            ).fetchone()
        return {
            "total": total,
            "helpful": positive,
            "corrected": corrections,
            "helpful_rate": round(positive / total, 4) if total else 0.0,
            "measured": before_count,
            "before_accuracy": round(before_sum / before_count, 4) if before_count else 0.0,
            "after_accuracy": round(after_sum / after_count, 4) if after_count else 0.0,
        }
