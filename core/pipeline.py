import time
from dataclasses import dataclass, field
from typing import Callable
from .adapters.base import Result, ExecutionError, Schema
from .validator import validate, ValidationError
from .retriever import BaseRetriever
from .knowledge import DomainKnowledgeEngine, KnowledgeContext
from .agents.classifier import QueryClassifier, QueryClassification
from .agents.critic import SemanticCritic


from .translator import translate_query


def schema_to_ddl(schema: Schema) -> str:
    lines = []
    for t in schema.tables:
        definitions = [f"{c.name} {c.type}" for c in t.columns]
        definitions.extend(
            f"FOREIGN KEY ({column}) REFERENCES {ref_table}({ref_column})"
            for column, ref_table, ref_column in t.foreign_keys
        )
        cols = ", ".join(definitions)
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
    retrieved_schema: Schema | None = None
    classification: QueryClassification | None = None
    knowledge_context: KnowledgeContext | None = None
    original_question: str | None = None
    is_translated: bool = False
    sql_history: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        adapter,
        generator,
        retriever: BaseRetriever | None = None,
        knowledge_engine: DomainKnowledgeEngine | None = None,
        classifier: QueryClassifier | None = None,
        critic: SemanticCritic | None = None,
        max_repairs: int = 1,
    ):
        self.adapter = adapter
        self.generator = generator
        self.retriever = retriever
        self.knowledge_engine = knowledge_engine
        self.classifier = classifier or QueryClassifier()
        self.critic = critic or SemanticCritic()
        self.max_repairs = max_repairs

    def ask(
        self,
        question: str,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> PipelineResult:
        def report(stage: str, message: str) -> None:
            if on_progress:
                on_progress(stage, message)

        # Multilingual Translation Layer
        report("translate", "Checking query language")
        t_t0 = time.perf_counter()
        en_question, was_translated, _ = translate_query(question)
        t_trans = round(time.perf_counter() - t_t0, 4)

        out = PipelineResult(
            question=en_question,
            original_question=question if was_translated else None,
            is_translated=was_translated,
        )
        if was_translated:
            out.timings["translate"] = t_trans

        full_schema = self.adapter.get_schema()

        # 1. Retrieval & Pruning
        report("schema", "Reading database schema")
        if self.retriever:
            t_r0 = time.perf_counter()
            schema = self.retriever.retrieve(en_question, full_schema)
            out.timings["retrieve"] = round(time.perf_counter() - t_r0, 4)
            out.retrieved_schema = schema
        else:
            schema = full_schema
            out.retrieved_schema = schema

        ddl = schema_to_ddl(schema)

        # 2. Knowledge Resolution (Values & Rules)
        report("classify", "Understanding the requested analysis")
        k_ctx = None
        if self.knowledge_engine:
            t_k0 = time.perf_counter()
            allowed = {t.name for t in schema.tables}
            k_ctx = self.knowledge_engine.resolve(en_question, allowed_tables=allowed)
            out.timings["knowledge"] = round(time.perf_counter() - t_k0, 4)
            out.knowledge_context = k_ctx

        # 3. Query Classification
        t_c0 = time.perf_counter()
        classification = self.classifier.classify(
            en_question, num_tables_linked=len(schema.tables)
        )
        out.timings["classify"] = round(time.perf_counter() - t_c0, 4)
        out.classification = classification

        feedback = None

        # 4. Generation & Multi-Turn Self-Repair Loop
        for attempt in range(self.max_repairs + 1):
            out.attempts = attempt + 1
            report("generate", f"Generating SQL (attempt {out.attempts})")
            t0 = time.perf_counter()
            try:
                # Check if generator supports decomposed draft() with context
                if hasattr(self.generator, "draft"):
                    sql = self.generator.draft(
                        question=en_question,
                        schema_ddl=ddl,
                        classification=classification,
                        knowledge=k_ctx,
                        feedback=feedback,
                    )
                else:
                    sql = self.generator.generate(en_question, ddl, feedback)
            except Exception as e:
                out.error = str(e)
                return out

            out.timings[f"generate_{attempt}"] = round(
                time.perf_counter() - t0, 3
            )
            out.sql = sql
            out.sql_history.append(sql)

            # 5. AST & Dialect Safety Validation
            report("validate", "Checking SQL safety")
            try:
                validate(sql, full_schema, self.adapter.dialect())
            except ValidationError as e:
                out.error = str(e)
                feedback = f"Previous SQL: {sql} | AST Validation Error: {e}"
                if attempt < self.max_repairs:
                    report("repair", "SQL needs repair; preparing another attempt")
                continue

            # 6. Read-Only Execution
            report("execute", "Running the read-only query")
            t1 = time.perf_counter()
            try:
                out.result = self.adapter.execute_readonly(sql)
                out.timings[f"execute_{attempt}"] = round(
                    time.perf_counter() - t1, 3
                )
            except ExecutionError as e:
                out.error = str(e)
                feedback = f"Previous SQL: {sql} | Database Error: {e}"
                if attempt < self.max_repairs:
                    report("repair", "Query failed; preparing a repaired SQL attempt")
                continue

            # 7. Semantic Critic Review
            if self.critic and attempt < self.max_repairs:
                review = self.critic.review(
                    question=question,
                    sql=sql,
                    result=out.result,
                    dialect=self.adapter.dialect(),
                )
                if not review.is_acceptable:
                    feedback = (
                        f"Previous SQL: {sql} | Semantic Issue: {review.feedback}"
                    )
                    report("repair", "Reviewing a corrected query")
                    continue

            out.error = None
            report("complete", "Query completed")
            return out

        report("complete", "Query finished with an error")
        return out
