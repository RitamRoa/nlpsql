import re
from .classifier import QueryClassification
from ..knowledge import KnowledgeContext
from ..few_shot import FewShotRetriever
from ..providers.base import LLMProvider


def clean_sql(text: str) -> str:
    """Extract clean SQL from model output, stripping markdown code fences."""
    text = re.sub(r"```(?:sql)?", "", text)
    text = text.replace("```", "").strip()
    # Match complete SELECT statement ending with semicolon if present
    m = re.search(r"(SELECT\s+[^;]+;)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Otherwise strip leading comments
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sql_lines = [l for l in lines if not l.startswith(("--", "//", "#"))]
    cleaned = " ".join(sql_lines).strip()
    if cleaned and not cleaned.endswith(";"):
        cleaned += ";"
    return cleaned


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
