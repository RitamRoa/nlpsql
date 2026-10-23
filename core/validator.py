import sqlglot
from sqlglot import exp
from .adapters.base import Schema


class ValidationError(Exception):
    pass


WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
)
BLOCKED_FUNCS = {"load_extension", "readfile", "writefile"}


def validate(sql: str, schema: Schema, dialect: str = "sqlite") -> None:
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise ValidationError(f"parse error: {e}") from e

    if len(statements) != 1:
        raise ValidationError("exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise ValidationError("only SELECT queries are allowed")

    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise ValidationError("write operations are not allowed")

    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() in BLOCKED_FUNCS:
            raise ValidationError(f"function not allowed: {fn.name}")

    known = {
        t.name.lower(): {c.name.lower() for c in t.columns} for t in schema.tables
    }
    all_cols = set().union(*known.values()) if known else set()
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}

    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if name not in cte_names and name not in known:
            raise ValidationError(f"unknown table: {t.name}")

    for col in tree.find_all(exp.Column):
        n = col.name.lower()
        if n == "*" or n in all_cols or n in select_aliases:
            continue
        raise ValidationError(f"unknown column: {col.name}")
