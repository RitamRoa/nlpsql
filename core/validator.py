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
BLOCKED_FUNCS = {
    "load_extension", "readfile", "writefile",
    # server-side file access / denial of service in Postgres and MySQL
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export",
    "pg_sleep", "sleep", "benchmark", "load_file",
}
# More than this many joins without an ON/USING condition is a cross-product blowup.
MAX_UNCONSTRAINED_JOINS = 1


def validate(sql: str, schema: Schema, dialect: str = "sqlite") -> None:
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        raise ValidationError(f"parse error: {e}") from e

    if len(statements) != 1:
        raise ValidationError("exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise ValidationError("only SELECT queries are allowed")

    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise ValidationError("write operations are not allowed")

    for with_ in tree.find_all(exp.With):
        if with_.args.get("recursive"):
            raise ValidationError("recursive CTEs are not allowed")

    unconstrained = [
        j for j in tree.find_all(exp.Join)
        if not j.args.get("on") and not j.args.get("using")
        and (j.side or "").upper() not in {"NATURAL"}
        and not isinstance(j.this, exp.Lateral)
    ]
    if len(unconstrained) > MAX_UNCONSTRAINED_JOINS:
        raise ValidationError("too many joins without a join condition (cartesian product)")

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

    # Qualified columns (e.salary) must exist on the table the qualifier names.
    # An alias that is reused for different tables in different scopes is skipped.
    bindings: dict[str, set[str]] = {}
    for t in tree.find_all(exp.Table):
        if t.name.lower() in cte_names or t.name.lower() not in known:
            continue
        bindings.setdefault((t.alias_or_name or t.name).lower(), set()).add(t.name.lower())
    for col in tree.find_all(exp.Column):
        qualifier = (col.table or "").lower()
        if not qualifier or col.name == "*" or qualifier in cte_names:
            continue
        targets = bindings.get(qualifier)
        if targets and len(targets) == 1:
            table = next(iter(targets))
            if col.name.lower() not in known[table]:
                raise ValidationError(f"unknown column: {qualifier}.{col.name}")
