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
    confidence: float | None = None
    confidence_agreement: float | None = None
    confidence_candidates: list[str] = field(default_factory=list)
    abstained: bool = False
    explanation: dict | None = None
    narration: str | None = None
    cost_guard: dict | None = None
    feature_errors: dict[str, str] = field(default_factory=dict)


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
        confidence_samples: int = 3,
        confidence_threshold: float = 2 / 3,
    ):
        if not 1 <= confidence_samples <= 5:
            raise ValueError("confidence_samples must be between 1 and 5.")
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1.")
        self.adapter = adapter
        self.generator = generator
        self.retriever = retriever
        self.knowledge_engine = knowledge_engine
        self.classifier = classifier or QueryClassifier()
        self.critic = critic or SemanticCritic()
        self.max_repairs = max_repairs
        self.confidence_samples = confidence_samples
        self.confidence_threshold = confidence_threshold

    def ask(
        self,
        question: str,
        on_progress: Callable[[str, str], None] | None = None,
        conversation_context: str | None = None,
        query_guard: Callable[[str, Schema], str] | None = None,
        result_transform: Callable[[Result, str], Result] | None = None,
        schema_override: Schema | None = None,
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
        visible_schema = schema_override or full_schema

        # 1. Retrieval & Pruning
        report("schema", "Reading database schema")
        if self.retriever:
            t_r0 = time.perf_counter()
            schema = self.retriever.retrieve(en_question, visible_schema)
            out.timings["retrieve"] = round(time.perf_counter() - t_r0, 4)
            out.retrieved_schema = schema
        else:
            schema = visible_schema
            out.retrieved_schema = schema

        ddl = schema_to_ddl(schema)

        # 2. Knowledge Resolution (Values & Rules)
        report("classify", "Understanding the requested analysis")
        k_ctx = None
        if self.knowledge_engine:
            t_k0 = time.perf_counter()
            allowed = {t.name for t in schema.tables}
            k_ctx = self.knowledge_engine.resolve(
                en_question,
                allowed_tables=allowed,
                allowed_schema=visible_schema,
            )
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

        def generate(candidate_temperature: float = 0.0) -> str:
            if hasattr(self.generator, "draft"):
                return self.generator.draft(
                    question=en_question,
                    schema_ddl=ddl,
                    classification=classification,
                    knowledge=k_ctx,
                    feedback=feedback,
                    temperature=candidate_temperature,
                    conversation_context=conversation_context,
                )
            return self.generator.generate(en_question, ddl, feedback)

        # 4. Generation & Multi-Turn Self-Repair Loop
        for attempt in range(self.max_repairs + 1):
            out.attempts = attempt + 1
            report("generate", f"Generating SQL (attempt {out.attempts})")
            t0 = time.perf_counter()
            try:
                sql = generate()
                if query_guard:
                    sql = query_guard(sql, full_schema)
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

            from .query_tools import cost_guard
            try:
                out.cost_guard = cost_guard(self.adapter, sql)
                out.feature_errors.pop("cost_guard", None)
            except (ExecutionError, ValueError) as e:
                out.feature_errors["cost_guard"] = str(e)

            # 6. Read-Only Execution
            report("execute", "Running the read-only query")
            t1 = time.perf_counter()
            try:
                out.result = self.adapter.execute_readonly(sql)
                if result_transform:
                    out.result = result_transform(out.result, sql)
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
            out.confidence_candidates = [sql]
            successful_results = [(sql, out.result)]
            confidence_started = time.perf_counter()
            report("confidence", "Checking agreement across candidate queries")
            for candidate_index in range(1, self.confidence_samples):
                try:
                    candidate_sql = generate(0.25 + 0.05 * (candidate_index % 3))
                    if query_guard:
                        candidate_sql = query_guard(candidate_sql, full_schema)
                    out.confidence_candidates.append(candidate_sql)
                    validate(candidate_sql, full_schema, self.adapter.dialect())
                    candidate_result = self.adapter.execute_readonly(candidate_sql)
                    if result_transform:
                        candidate_result = result_transform(candidate_result, candidate_sql)
                    successful_results.append((candidate_sql, candidate_result))
                except Exception:
                    out.confidence_candidates.append("")

            def result_key(result: Result) -> tuple:
                return (
                    tuple(result.columns),
                    tuple(sorted((repr(tuple(row)) for row in result.rows))),
                    result.truncated,
                )

            groups: dict[tuple, list[tuple[str, Result]]] = {}
            for candidate in successful_results:
                groups.setdefault(result_key(candidate[1]), []).append(candidate)
            winner = max(groups.values(), key=len)
            score = len(winner) / self.confidence_samples
            out.confidence = score
            out.confidence_agreement = score
            if score < self.confidence_threshold:
                out.result = None
                out.abstained = True
                out.error = "I'm not sure the candidate queries agree enough to return a reliable answer."
                report("complete", "Candidate query results disagree; abstaining")
                return out
            winner_sql, winner_result = winner[0]
            out.sql = winner_sql
            out.result = winner_result
            out.timings["confidence"] = round(
                time.perf_counter() - confidence_started, 4
            )
            from .query_tools import explain_query, verified_narration

            try:
                explanation = explain_query(winner_sql, self.adapter.dialect())
                out.explanation = {
                    "summary": explanation.summary,
                    "tables": explanation.tables,
                    "columns": explanation.columns,
                    "trace": explanation.trace,
                }
            except ValueError as error:
                out.feature_errors["explanation"] = str(error)
            out.narration = verified_narration(winner_result)
            report("complete", "Query completed")
            return out

        report("complete", "Query finished with an error")
        return out
