import time
from dataclasses import dataclass, field
from .adapters.base import Result, ExecutionError, Schema
from .validator import validate, ValidationError


def schema_to_ddl(schema: Schema) -> str:
    lines = []
    for t in schema.tables:
        cols = ", ".join(f"{c.name} {c.type}" for c in t.columns)
        lines.append(f"CREATE TABLE {t.name} ({cols});")
    return "\n".join(lines)


@dataclass
class PipelineResult:
    question: str
    sql: str | None = None
    result: Result | None = None
    error: str | None = None
    attempts: int = 0
    timings: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, adapter, generator, max_repairs: int = 1):
        self.adapter = adapter
        self.generator = generator
        self.max_repairs = max_repairs

    def ask(self, question: str) -> PipelineResult:
        out = PipelineResult(question)
        schema = self.adapter.get_schema()
        ddl = schema_to_ddl(schema)
        feedback = None

        for attempt in range(self.max_repairs + 1):
            out.attempts = attempt + 1
            t0 = time.perf_counter()
            sql = self.generator.generate(question, ddl, feedback)
            out.timings[f"generate_{attempt}"] = round(time.perf_counter() - t0, 3)
            out.sql = sql
            try:
                validate(sql, schema, self.adapter.dialect())
                t1 = time.perf_counter()
                out.result = self.adapter.execute_readonly(sql)
                out.timings[f"execute_{attempt}"] = round(
                    time.perf_counter() - t1, 3
                )
                out.error = None
                return out
            except (ValidationError, ExecutionError) as e:
                out.error = str(e)
                feedback = f"Previous SQL: {sql} | Error: {e}"
        return out
