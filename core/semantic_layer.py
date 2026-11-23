"""Editable semantic metadata for database schemas."""
import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .adapters.base import Schema


@dataclass
class ColumnDefinition:
    description: str = ""
    synonyms: list[str] = field(default_factory=list)


@dataclass
class TableDefinition:
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    columns: dict[str, ColumnDefinition] = field(default_factory=dict)


@dataclass
class MetricDefinition:
    name: str
    description: str
    expression: str
    synonyms: list[str] = field(default_factory=list)


@dataclass
class RuleDefinition:
    name: str
    description: str
    sql_expression: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class SemanticLayer:
    tables: dict[str, TableDefinition] = field(default_factory=dict)
    metrics: list[MetricDefinition] = field(default_factory=list)
    business_rules: list[RuleDefinition] = field(default_factory=list)

    def table(self, name: str) -> TableDefinition | None:
        return next(
            (definition for key, definition in self.tables.items()
             if key.lower() == name.lower()),
            None,
        )

    def validate_against(self, schema: Schema) -> None:
        """Raise a clear error if the catalog references missing tables or columns."""
        schema_tables = {table.name.lower(): table for table in schema.tables}
        for table_name, definition in self.tables.items():
            table = schema_tables.get(table_name.lower())
            if table is None:
                raise ValueError(f"Semantic layer references unknown table '{table_name}'.")
            schema_columns = {column.name.lower() for column in table.columns}
            for column_name in definition.columns:
                if column_name.lower() not in schema_columns:
                    raise ValueError(
                        f"Semantic layer references unknown column "
                        f"'{table_name}.{column_name}'."
                    )


def _string_list(value: object, location: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{location} must be a list of non-empty strings.")
    return value


def _required_string(mapping: dict, key: str, location: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string.")
    return value.strip()


def _optional_string(mapping: dict, key: str, location: str) -> str:
    value = mapping.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{location}.{key} must be a string.")
    return value.strip()


def load_semantic_layer(path: str | Path) -> SemanticLayer:
    """Load and validate a YAML semantic layer."""
    layer_path = Path(path)
    try:
        with layer_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid semantic layer YAML in '{layer_path}': {error}") from error

    if not isinstance(data, dict):
        raise ValueError(f"Semantic layer '{layer_path}' must contain a YAML mapping.")
    unknown_keys = set(data) - {"version", "tables", "metrics", "business_rules"}
    if unknown_keys:
        raise ValueError(
            f"Unknown semantic layer fields in '{layer_path}': "
            f"{', '.join(sorted(map(str, unknown_keys)))}."
        )
    if data.get("version") != 1:
        raise ValueError(f"Semantic layer '{layer_path}' must use version: 1.")

    tables_data = data.get("tables", {})
    if not isinstance(tables_data, dict):
        raise ValueError(f"Semantic layer '{layer_path}' tables must be a mapping.")
    tables: dict[str, TableDefinition] = {}
    for table_name, raw_table in tables_data.items():
        location = f"tables.{table_name}"
        if not isinstance(table_name, str) or not table_name.strip() or not isinstance(raw_table, dict):
            raise ValueError(f"{location} must be a table name mapped to a mapping.")
        raw_columns = raw_table.get("columns", {})
        if not isinstance(raw_columns, dict):
            raise ValueError(f"{location}.columns must be a mapping.")
        columns: dict[str, ColumnDefinition] = {}
        for column_name, raw_column in raw_columns.items():
            column_location = f"{location}.columns.{column_name}"
            if not isinstance(column_name, str) or not column_name.strip() or not isinstance(raw_column, dict):
                raise ValueError(f"{column_location} must be a column name mapped to a mapping.")
            columns[column_name] = ColumnDefinition(
                description=_optional_string(raw_column, "description", column_location),
                synonyms=_string_list(raw_column.get("synonyms", []), f"{column_location}.synonyms"),
            )
        tables[table_name] = TableDefinition(
            description=_optional_string(raw_table, "description", location),
            synonyms=_string_list(raw_table.get("synonyms", []), f"{location}.synonyms"),
            columns=columns,
        )

    metrics_data = data.get("metrics", [])
    if not isinstance(metrics_data, list):
        raise ValueError(f"Semantic layer '{layer_path}' metrics must be a list.")
    metrics = []
    for index, raw_metric in enumerate(metrics_data):
        location = f"metrics[{index}]"
        if not isinstance(raw_metric, dict):
            raise ValueError(f"{location} must be a mapping.")
        metrics.append(MetricDefinition(
            name=_required_string(raw_metric, "name", location),
            description=_required_string(raw_metric, "description", location),
            expression=_required_string(raw_metric, "expression", location),
            synonyms=_string_list(raw_metric.get("synonyms", []), f"{location}.synonyms"),
        ))

    rules_data = data.get("business_rules", [])
    if not isinstance(rules_data, list):
        raise ValueError(f"Semantic layer '{layer_path}' business_rules must be a list.")
    rules = []
    for index, raw_rule in enumerate(rules_data):
        location = f"business_rules[{index}]"
        if not isinstance(raw_rule, dict):
            raise ValueError(f"{location} must be a mapping.")
        rules.append(RuleDefinition(
            name=_required_string(raw_rule, "name", location),
            description=_required_string(raw_rule, "description", location),
            sql_expression=_required_string(raw_rule, "sql_expression", location),
            keywords=_string_list(raw_rule.get("keywords", []), f"{location}.keywords"),
        ))
    return SemanticLayer(tables=tables, metrics=metrics, business_rules=rules)


def _humanize(identifier: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier)
    return " ".join(re.sub(r"[_\W]+", " ", spaced).split()).lower()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _metric_key(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", "_".join(parts).lower()).strip("_")


def _is_identifier_metric(metric: MetricDefinition, schema: Schema) -> bool:
    for table in schema.tables:
        foreign_keys = {column.lower() for column, _, _ in table.foreign_keys}
        for column in table.columns:
            is_identifier = column.name.lower() == "id" or column.name.lower().endswith("_id")
            if not (column.is_pk or is_identifier or column.name.lower() in foreign_keys):
                continue
            if metric.name.lower() in {
                _metric_key(table.name, column.name, "total"),
                _metric_key(table.name, column.name, "average"),
            }:
                return True
    return False


def generate_semantic_layer(
    schema: Schema,
    existing: SemanticLayer | None = None,
) -> SemanticLayer:
    """Infer editable table/column documentation and starter metrics from a schema."""
    existing = existing or SemanticLayer()
    existing_tables = {name.lower(): value for name, value in existing.tables.items()}
    generated_tables: dict[str, TableDefinition] = {}
    metrics = [
        metric for metric in existing.metrics
        if not _is_identifier_metric(metric, schema)
    ]
    metric_names = {metric.name.lower() for metric in metrics}

    for table in schema.tables:
        old_table = existing_tables.get(table.name.lower())
        old_columns = (
            {name.lower(): value for name, value in old_table.columns.items()}
            if old_table else {}
        )
        table_words = _humanize(table.name)
        columns: dict[str, ColumnDefinition] = {}
        foreign_keys = {column.lower(): ref_table for column, ref_table, _ in table.foreign_keys}
        for column in table.columns:
            old_column = old_columns.get(column.name.lower())
            if old_column and old_column.description:
                description = old_column.description
            elif column.is_pk:
                description = f"Unique identifier for this {table_words} record."
            elif column.name.lower() in foreign_keys:
                description = (
                    f"Identifier of the related "
                    f"{_humanize(foreign_keys[column.name.lower()])} record."
                )
            else:
                description = f"{_humanize(column.name).capitalize()} ({column.type.lower()})."
            columns[column.name] = ColumnDefinition(
                description=description,
                synonyms=list(old_column.synonyms) if old_column else [],
            )

            col_type = column.type.upper()
            is_numeric = any(
                kind in col_type for kind in ("INT", "REAL", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE")
            )
            is_identifier = column.name.lower() == "id" or column.name.lower().endswith("_id")
            if is_numeric and not column.is_pk and not is_identifier and column.name.lower() not in foreign_keys:
                expression = (
                    f"SUM({_quote_identifier(table.name)}.{_quote_identifier(column.name)})"
                )
                metric_name = _metric_key(table.name, column.name, "total")
                if metric_name not in metric_names:
                    metric_names.add(metric_name)
                    metrics.append(MetricDefinition(
                        name=metric_name,
                        description=f"Total {_humanize(column.name)} across {_humanize(table.name)}.",
                        expression=expression,
                        synonyms=[
                            f"total {_humanize(column.name)}",
                            f"sum {_humanize(column.name)}",
                            f"sum of {_humanize(column.name)}",
                        ],
                    ))
                average_name = _metric_key(table.name, column.name, "average")
                if average_name not in metric_names:
                    metric_names.add(average_name)
                    metrics.append(MetricDefinition(
                        name=average_name,
                        description=f"Average {_humanize(column.name)} across {_humanize(table.name)}.",
                        expression=(
                            f"AVG({_quote_identifier(table.name)}.{_quote_identifier(column.name)})"
                        ),
                        synonyms=[
                            f"average {_humanize(column.name)}",
                            f"mean {_humanize(column.name)}",
                        ],
                    ))

        generated_tables[table.name] = TableDefinition(
            description=(
                old_table.description if old_table and old_table.description
                else f"Records for {_humanize(table.name)}."
            ),
            synonyms=list(old_table.synonyms) if old_table else [],
            columns=columns,
        )

    return SemanticLayer(
        tables=generated_tables,
        metrics=metrics,
        business_rules=list(existing.business_rules),
    )


def write_semantic_layer(layer: SemanticLayer, path: str | Path) -> None:
    """Write semantic metadata as readable YAML."""
    data = {
        "version": 1,
        "tables": {
            table_name: {
                "description": table.description,
                **({"synonyms": table.synonyms} if table.synonyms else {}),
                "columns": {
                    column_name: {
                        "description": column.description,
                        **({"synonyms": column.synonyms} if column.synonyms else {}),
                    }
                    for column_name, column in table.columns.items()
                },
            }
            for table_name, table in layer.tables.items()
        },
        "metrics": [
            {
                "name": metric.name,
                "description": metric.description,
                "expression": metric.expression,
                **({"synonyms": metric.synonyms} if metric.synonyms else {}),
            }
            for metric in layer.metrics
        ],
        "business_rules": [
            {
                "name": rule.name,
                "description": rule.description,
                "sql_expression": rule.sql_expression,
                "keywords": rule.keywords,
            }
            for rule in layer.business_rules
        ],
    }
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=True, width=100)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate editable semantic YAML documentation for a database schema."
    )
    parser.add_argument("--schema", default="company", help="Built-in schema name.")
    parser.add_argument("--output", help="Output YAML path.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate table and column documentation while preserving existing rules and metrics.",
    )
    args = parser.parse_args()

    from .loader import DATA_DIR, create_adapter

    output_path = Path(args.output) if args.output else Path(DATA_DIR) / "semantic" / f"{args.schema}.yaml"
    if output_path.exists() and not args.force:
        parser.error(f"{output_path} already exists; use --force to regenerate it.")
    adapter = create_adapter(schema_name=args.schema)
    try:
        existing = load_semantic_layer(output_path) if output_path.exists() else SemanticLayer()
        layer = generate_semantic_layer(adapter.get_schema(), existing)
        write_semantic_layer(layer, output_path)
    finally:
        adapter.close()
    print(f"Wrote semantic layer to {output_path}")


if __name__ == "__main__":
    main()
