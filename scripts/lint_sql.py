r"""Static SQL and schema checker (needs NO database).

  python scripts\lint_sql.py

It builds the schema from database\schema.sql plus every migration, then reads every SQL string passed to
execute() / query_one() / query_all() in app\ and scripts\ and checks that:
  * every table exists and every  alias.column  (and INSERT / UPDATE column) exists in that table
  * the number of %s placeholders matches the literal parameter tuple, and INSERT column and value counts agree
  * every FOREIGN KEY in the schema points at an existing table and column
It cannot prove a query returns the right rows, only that names and counts are consistent, so it catches typos
that would otherwise only appear as MySQL errors. A clean run is NOT a substitute for the MySQL integration tests.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)
CALLS = {"execute", "query_one", "query_all"}
NOT_ALIAS = {"ON", "WHERE", "SET", "LEFT", "RIGHT", "INNER", "JOIN", "GROUP", "ORDER", "LIMIT", "FOR", "USING", "AND", "OR",
             "VALUES", "SELECT", "UNION", "HAVING", "AS", "CROSS", "STRAIGHT_JOIN", "OFFSET", "NATURAL", "FROM", "X", "SHARE"}
CONSTRAINT_WORDS = ("PRIMARY", "UNIQUE", "KEY", "CONSTRAINT", "FULLTEXT", "INDEX", "CHECK", "FOREIGN")


def strip_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


def split_top(text, sep=","):
    parts, depth, cur, quote = [], 0, [], None
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


RISKY_ACTIONS = re.compile(r"ON\s+(?:DELETE|UPDATE)\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT)", re.I)
check_conflicts = []          # filled by parse_create_tables: (table, constraint, column)


def parse_create_tables(sql, tables, fks):
    for m in re.finditer(r"CREATE TABLE\s+(\w+)\s*\(", sql):
        start, depth, i = m.end(), 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        body = sql[start:i - 1]
        cols = set()
        risky_fk_cols, checks = set(), []
        for part in split_top(body):
            first = part.split()[0].strip("`")
            if first.upper() in CONSTRAINT_WORDS:
                fk_cols = re.search(r"FOREIGN KEY\s*\(([^)]*)\)", part)
                if fk_cols and RISKY_ACTIONS.search(part):
                    risky_fk_cols |= {c.strip() for c in fk_cols.group(1).split(",")}
                chk = re.search(r"CONSTRAINT\s+(\w+)\s+CHECK\s*\((.*)\)\s*$", part, re.S)
                if chk:
                    checks.append((chk.group(1), set(re.findall(r"[A-Za-z_]\w*", chk.group(2)))))
                for fk in re.finditer(r"FOREIGN KEY\s*\((\w+)\)\s*REFERENCES\s+(\w+)\s*\((\w+)\)", part):
                    fks.append((m.group(1), fk.group(1), fk.group(2), fk.group(3)))
            else:
                cols.add(first)
        tables[m.group(1)] = cols
        for name, words in checks:
            for col in sorted(words & risky_fk_cols):
                check_conflicts.append((m.group(1), name, col))


def build_schema():
    tables, fks = {}, []
    parse_create_tables(strip_comments((ROOT / "database" / "schema.sql").read_text(encoding="utf8")), tables, fks)
    for path in sorted((ROOT / "database" / "migrations").glob("[0-9][0-9][0-9]_*.sql")):
        sql = strip_comments(path.read_text(encoding="utf8"))
        parse_create_tables(sql, tables, fks)
        for stmt in sql.split(";"):
            alter = re.match(r"\s*ALTER TABLE\s+(\w+)(.*)", stmt, re.S)
            if alter:
                for col in re.finditer(r"ADD COLUMN\s+(\w+)", alter.group(2)):
                    tables[alter.group(1)].add(col.group(1))
                for fk in re.finditer(r"FOREIGN KEY\s*\((\w+)\)\s*REFERENCES\s+(\w+)\s*\((\w+)\)", alter.group(2)):
                    fks.append((alter.group(1), fk.group(1), fk.group(2), fk.group(3)))
    tables["schema_migrations"] = {"version", "applied_at"}               # created by scripts\migrate.py
    return tables, fks


def check_foreign_keys(tables, fks):
    problems = []
    for table, col, ref_table, ref_col in fks:
        if ref_table not in tables:
            problems.append(f"schema: {table}.{col} references unknown table {ref_table}")
        elif ref_col not in tables[ref_table]:
            problems.append(f"schema: {table}.{col} references unknown column {ref_table}.{ref_col}")
        if col not in tables.get(table, ()):
            problems.append(f"schema: foreign key column {table}.{col} does not exist")
    return problems


# ------------------------------------------------------------------ Python SQL extraction
def module_constants(tree):
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = const_str(node.value, consts)
            if value is not None:
                consts[node.targets[0].id] = value
    return consts


def const_str(node, consts):
    """The string an expression builds, with {X} marking anything computed at run time. None if not a string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{X}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = const_str(node.left, consts), const_str(node.right, consts)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return const_str(node.left, consts)
    if isinstance(node, ast.Name):
        return consts.get(node.id, "{X}")
    if isinstance(node, (ast.Attribute, ast.Call, ast.IfExp, ast.Subscript)):
        return "{X}"
    return None


def tuple_len(node):
    if isinstance(node, ast.Tuple) and not any(isinstance(e, ast.Starred) for e in node.elts):
        return len(node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = tuple_len(node.left), tuple_len(node.right)
        return a + b if a is not None and b is not None else None
    return None


def extract(path):
    tree = ast.parse(path.read_text(encoding="utf8"))
    consts = module_constants(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name not in CALLS or not node.args:
            continue
        sql = const_str(node.args[0], consts)
        if sql and SQL_START.match(sql):
            params = node.args[1] if len(node.args) > 1 else None
            found.append((node.lineno, sql, params))
    return found


# ------------------------------------------------------------------ one statement
def check_statement(where, sql, params, tables):
    problems = []
    flat = re.sub(r"\s+", " ", sql)
    dynamic = "{X}" in flat
    aliases = {t: {t} for t in tables}
    flat_tables = re.sub(r"\bFOR\s+(UPDATE|SHARE)(\s+OF\s+\w+)?", " ", flat, flags=re.I)       # row-lock clause, not a table
    for m in re.finditer(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+`?(\w+)`?(?:\s+(?:AS\s+)?(\w+))?", flat_tables, re.I):
        table, alias = m.group(1), m.group(2)
        if table.upper() in NOT_ALIAS or table == "X":
            continue
        if table not in tables:
            if table.lower() not in ("information_schema", "dual"):
                problems.append(f"{where}: unknown table '{table}'")
            continue
        if alias and alias.upper() not in NOT_ALIAS and not alias.isdigit():
            aliases.setdefault(alias, set()).add(table)
    for m in re.finditer(r"(?<![\w$.'])(\w+)\.(\w+)\b", flat):
        left, col = m.groups()
        if left in aliases and not left.isdigit():
            if not any(col in tables[t] for t in aliases[left]):
                problems.append(f"{where}: column '{left}.{col}' not found in {sorted(aliases[left])}")
    ins = re.match(r"\s*INSERT\s+(?:IGNORE\s+)?INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\((.*)\)\s*(?:ON DUPLICATE.*)?$", flat, re.I)
    if ins and ins.group(1) in tables:
        cols = [c.strip().strip("`") for c in ins.group(2).split(",")]
        for c in cols:
            if c not in tables[ins.group(1)]:
                problems.append(f"{where}: INSERT column '{ins.group(1)}.{c}' not found")
        values = split_top(ins.group(3))
        if not dynamic and len(values) != len(cols):
            problems.append(f"{where}: INSERT lists {len(cols)} columns but {len(values)} values")
    upd = re.match(r"\s*UPDATE\s+(\w+)(?:\s+(\w+))?\s+SET\s+(.*?)(?:\s+WHERE\s+.*)?$", flat, re.I)
    if upd and upd.group(1) in tables:
        for assignment in split_top(upd.group(3)):
            target = assignment.split("=")[0].strip().strip("`")
            target = target.split(".")[-1]
            if target and re.fullmatch(r"\w+", target) and target not in tables[upd.group(1)]:
                problems.append(f"{where}: UPDATE sets unknown column '{upd.group(1)}.{target}'")
    if not dynamic and params is not None:
        expected = len(re.findall(r"%s", flat))
        given = tuple_len(params)
        if given is not None and given != expected:
            problems.append(f"{where}: {expected} placeholders but {given} parameters")
    elif not dynamic and params is None and "%s" in flat:
        problems.append(f"{where}: placeholders but no parameters passed")
    return problems


def lint():
    tables, fks = build_schema()
    problems = check_foreign_keys(tables, fks)
    problems += [f"schema: CHECK {c} on {t} uses column '{col}', which has a FOREIGN KEY CASCADE/SET NULL action "
                 f"(MySQL error 3823)" for t, c, col in check_conflicts]
    checked = 0
    for folder in ("app", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            for line, sql, params in extract(path):
                checked += 1
                problems += check_statement(f"{path.relative_to(ROOT)}:{line}", sql, params, tables)
    return problems, checked, len(tables), len(fks)


def main():
    problems, checked, n_tables, n_fks = lint()
    print(f"Schema: {n_tables} tables, {n_fks} foreign keys. SQL statements checked: {checked}.")
    for p in problems:
        print("PROBLEM:", p)
    print("OK: no problems found." if not problems else f"{len(problems)} problem(s) found.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
