#!/usr/bin/env python3
"""Regenerate the source navigation map without importing application modules."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
rows = [
    "# Code navigation map",
    "",
    "Generated from the Python source by `python3 scripts/generate_reference.py`. "
    "Use [ARCHITECTURE.md](ARCHITECTURE.md) for execution flow and this map to locate an implementation. "
    "Only public methods are listed; internal helpers remain in the linked module.",
    "",
]
route_rows = []
for path in sorted(APP.rglob("*.py")):
    if "__pycache__" in path.parts or path.name == "__init__.py":
        continue
    tree = ast.parse(path.read_text())
    rel = path.relative_to(ROOT).as_posix()
    objects = []
    prefixes = {}
    for assignment in tree.body:
        if (
            isinstance(assignment, ast.Assign)
            and isinstance(assignment.value, ast.Call)
            and isinstance(assignment.value.func, ast.Name)
            and assignment.value.func.id == "APIRouter"
        ):
            for keyword in assignment.value.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    for target in assignment.targets:
                        if isinstance(target, ast.Name):
                            prefixes[target.id] = keyword.value.value
    for item in tree.body:
        if isinstance(item, ast.ClassDef):
            methods = [
                n.name + "()"
                for n in item.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (not n.name.startswith("_") or n.name == "__init__")
            ]
            objects.append(
                (
                    item.name,
                    ", ".join("`" + m + "`" for m in methods)
                    or "Declarative model, schema or state definition",
                )
            )
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith(
            "_"
        ):
            objects.append((item.name + "()", "Module function"))
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in item.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in ("get", "post", "put", "patch", "delete")
                    and decorator.args
                ):
                    try:
                        route = ast.literal_eval(decorator.args[0])
                    except (ValueError, TypeError):
                        continue
                    router_name = (
                        decorator.func.value.id
                        if isinstance(decorator.func.value, ast.Name)
                        else ""
                    )
                    route_rows.append(
                        (
                            decorator.func.attr.upper(),
                            prefixes.get(router_name, "") + route,
                            rel,
                            item.name,
                        )
                    )
    if not objects:
        continue
    rows += [f"## [{rel}](../{rel})", "", "| Object | Public methods / role |", "| --- | --- |"]
    rows += [f"| `{name}` | {methods} |" for name, methods in objects]
    rows += [""]
rows += [
    "## HTTP route locator",
    "",
    "Machine-readable request/response schemas are available at `/api/openapi.json` on the running application.",
    "",
    "| Method | Route | Handler |",
    "| --- | --- | --- |",
]
for method, route, path, name in sorted(route_rows, key=lambda r: (r[1], r[0])):
    rows.append(f"| {method} | `{route}` | [{name}()](../{path}) |")
rows += [""]
(ROOT / "docs" / "CODE_MAP.md").write_text("\n".join(rows))
print(f"Generated docs/CODE_MAP.md with {len(route_rows)} route entries.")

# Optional ORM schema output requires the application's Python dependencies.
import sys

if "--schema" in sys.argv:
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable, CreateIndex
    from app.models import Base

    header = "-- V-OptimAIse ORM reference DDL. Apply Alembic migrations for installation.\n-- Generated from model metadata; no deployment database read.\nCREATE EXTENSION IF NOT EXISTS vector;\n\n"
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(
            str(CreateTable(table).compile(dialect=postgresql.dialect())).strip() + ";"
        )
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            statements.append(
                str(CreateIndex(index).compile(dialect=postgresql.dialect())).strip() + ";"
            )
    (ROOT / "docs" / "schema.sql").write_text(
        "\n".join(line.rstrip() for line in (header + "\n\n".join(statements)).splitlines()) + "\n"
    )
    classes = {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}
    table_rows = [
        "| Table | ORM class | Columns | Foreign-key targets |",
        "| --- | --- | ---: | --- |",
    ]
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        cls = classes.get(table.name)
        targets = ", ".join(sorted({str(f.target_fullname) for f in table.foreign_keys})) or "—"
        label = (cls.__module__ + "." + cls.__name__) if cls else "SQL table"
        table_rows.append(f"| `{table.name}` | `{label}` | {len(table.columns)} | {targets} |")
    guide = ROOT / "docs" / "SCHEMA.md"
    source = guide.read_text()
    start = source.index("## Table locator\n")
    end = source.index("## Relationships and lifecycle\n")
    guide.write_text(
        source[:start] + "## Table locator\n\n" + "\n".join(table_rows) + "\n\n" + source[end:]
    )
    print(f"Generated schema reference for {len(Base.metadata.tables)} tables.")
