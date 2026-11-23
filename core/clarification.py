"""Detect ambiguous business terms that map to multiple semantic columns."""
import re

from .semantic_layer import SemanticLayer


def find_ambiguous_terms(question: str, layer: SemanticLayer) -> list[dict]:
    normalized_question = " " + " ".join(
        re.findall(r"[a-z0-9]+", question.lower())
    ) + " "
    matches: dict[str, set[str]] = {}
    for table_name, table in layer.tables.items():
        for column_name, column in table.columns.items():
            phrases = [column_name.replace("_", " "), *column.synonyms]
            for phrase in phrases:
                normalized_phrase = " ".join(re.findall(r"[a-z0-9]+", phrase.lower()))
                if len(normalized_phrase) < 3:
                    continue
                if f" {normalized_phrase} " in normalized_question:
                    matches.setdefault(normalized_phrase, set()).add(
                        f"{table_name}.{column_name}"
                    )

    return [
        {
            "term": term,
            "choices": sorted(columns),
            "question": f"Which field do you mean by '{term}'?",
        }
        for term, columns in sorted(matches.items())
        if len(columns) > 1
    ]
