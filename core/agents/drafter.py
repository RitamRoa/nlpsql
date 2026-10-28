import re
from .classifier import QueryClassification
from ..knowledge import KnowledgeContext
from ..few_shot import FewShotRetriever
from ..providers.base import LLMProvider


def sanitize_dialect_syntax(sql: str) -> str:
    """Translates MySQL/Postgres dialect artifacts (DATE_SUB, INTERVAL, CURDATE) into SQLite."""
    # DATE_SUB(CURDATE(), INTERVAL X UNIT) -> date('now', '-X unit')
    sql = re.sub(
        r"DATE_SUB\s*\(\s*(?:CURDATE\(\)|CURRENT_DATE|NOW\(\))\s*,\s*INTERVAL\s+(\d+)\s+([A-Za-z]+)\s*\)",
        r"date('now', '-\1 \2')",
        sql,
        flags=re.IGNORECASE,
    )
    # DATE_ADD(CURDATE(), INTERVAL X UNIT) -> date('now', '+X unit')
    sql = re.sub(
        r"DATE_ADD\s*\(\s*(?:CURDATE\(\)|CURRENT_DATE|NOW\(\))\s*,\s*INTERVAL\s+(\d+)\s+([A-Za-z]+)\s*\)",
        r"date('now', '+\1 \2')",
        sql,
        flags=re.IGNORECASE,
    )
    # CURDATE() or NOW() -> date('now')
    sql = re.sub(r"\b(?:CURDATE|NOW)\s*\(\s*\)", "date('now')", sql, flags=re.IGNORECASE)
    # INTERVAL X UNIT -> 'X unit'
    sql = re.sub(r"\bINTERVAL\s+(\d+)\s+([A-Za-z]+)", r"'\1 \2'", sql, flags=re.IGNORECASE)
    return sql


def clean_sql(text: str) -> str:
    """Extract clean SQL from model output, stripping markdown code fences and dialect quirks."""
    text = re.sub(r"```(?:sql)?", "", text)
    text = text.replace("```", "").strip()
    # Match complete SELECT statement ending with semicolon if present
    m = re.search(r"(SELECT\s+[^;]+;)", text, re.IGNORECASE)
    if m:
        sql = m.group(1).strip()
    else:
        # Otherwise strip leading comments
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        sql_lines = [l for l in lines if not l.startswith(("--", "//", "#"))]
        sql = " ".join(sql_lines).strip()
        if sql and not sql.endswith(";"):
            sql += ";"
    return sanitize_dialect_syntax(sql)


def detect_direct_collection_query(question: str, schema_ddl: str) -> str | None:
    """Detects direct requests for entire tables (e.g. 'Get all customers') with 100% precision."""
    q_clean = question.strip().lower()
    m = re.match(r"^(?:get|list|show|find|select|display)\s+all\s+([a-z_]+)\s*[\?\.]?$", q_clean)
    if not m:
        m = re.match(r"^all\s+([a-z_]+)\s*[\?\.]?$", q_clean)
    if m:
        target = m.group(1).rstrip("s")
        # Extract table names from CREATE TABLE <name> in schema_ddl
        tbl_names = re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)", schema_ddl, re.IGNORECASE)
        for tbl in tbl_names:
            if target == tbl.lower().rstrip("s"):
                return f"SELECT * FROM {tbl};"
    return None


class SQLDrafter:
    """Agent responsible for synthesizing SQL using grounded DDL, domain knowledge,

    intent classification, and dynamic few-shot in-context demonstrations.
    """

    def __init__(
        self,
        provider: LLMProvider,
        few_shot_retriever: FewShotRetriever | None = None,
    ):
        self.provider = provider
        self.few_shot_retriever = few_shot_retriever or FewShotRetriever()

    def draft(
        self,
        question: str,
        schema_ddl: str,
        classification: QueryClassification,
        knowledge: KnowledgeContext | None = None,
        feedback: str | None = None,
    ) -> str:
        # Fast-path for direct collection queries without filters
        if not feedback:
            direct = detect_direct_collection_query(question, schema_ddl)
            if direct:
                return direct
        if classification.complexity == "EASY" and not feedback and not (knowledge and knowledge.to_prompt_text()):
            full_prompt = (
                "<|im_start|>system\n"
                "You are a helpful assistant who answers questions about database tables by responding with SQL queries.<|im_end|>\n"
                "<|im_start|>user\n"
                "Given the following database schema:\n"
                f"{schema_ddl}\n\n"
                "Answer the following question with a SQL query:\n"
                f"{question}<|im_end|>\n"
                "<|im_start|>assistant\n"
            )
            raw_text = self.provider.generate_text(
                prompt=full_prompt,
                stop=["<|im_end|>", ";\n\n", "```"],
                max_tokens=150,
                temperature=0.0,
            )
            return clean_sql(raw_text)

        prompt_parts = [
            "<|im_start|>system",
            "You are an expert Text-to-SQL assistant. You write clean, valid SQL queries based on the provided schema.",
            "<|im_end|>",
            "<|im_start|>user",
            "### Database Schema:",
            schema_ddl,
        ]

        if knowledge:
            k_text = knowledge.to_prompt_text()
            if k_text:
                prompt_parts.extend(["", "### Domain Knowledge & Literal Values:", k_text])

        # Add dynamic few-shot examples
        few_shot_text = self.few_shot_retriever.format_for_prompt(question)
        if few_shot_text:
            prompt_parts.extend(["", "### Reference Examples:", few_shot_text])

        # Add classification hints
        hints = []
        if classification.requires_join:
            hints.append("Query likely requires a JOIN across related tables.")
        if classification.requires_aggregation:
            hints.append(
                "Query involves aggregation (COUNT, SUM, AVG) with appropriate GROUP BY."
            )
        if hints:
            prompt_parts.extend(["", "### Guidelines:"] + [f"- {h}" for h in hints])

        prompt_parts.extend(["", f"### Question: {question}"])

        if feedback:
            prompt_parts.extend(
                [
                    "",
                    "### Feedback from Previous Attempt:",
                    f"-- {feedback}",
                    "-- Fix the error and produce the corrected SQL.",
                ]
            )

        prompt_parts.extend(["<|im_end|>", "<|im_start|>assistant\n"])
        full_prompt = "\n".join(prompt_parts)

        raw_text = self.provider.generate_text(
            prompt=full_prompt,
            stop=["<|im_end|>", ";\n\n", "```"],
            max_tokens=150,
            temperature=0.0,
        )

        return clean_sql(raw_text)
