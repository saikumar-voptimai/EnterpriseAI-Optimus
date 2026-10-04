"""Read-only PostgreSQL data source for workspace agents.

No caller-supplied SQL: queries are generated from a structured request whose
table, column and operator names are checked against the live catalog and the
administrator's table allowlist; values are bound parameters. Every query runs
in a READ ONLY transaction with a statement timeout. Use a database role that
only has SELECT on the shared tables as well.
"""

import asyncio
import datetime as dt
import decimal
import uuid
import psycopg
from psycopg import sql
from app.connectors.base import validate_host
from app.services.errors import ProviderError, ServiceError

OPERATORS = {"=": "=", "!=": "<>", ">": ">", ">=": ">=", "<": "<", "<=": "<=", "contains": "ILIKE"}
AGGREGATES = {"count", "sum", "avg", "min", "max"}
MAX_ROWS = 500
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema")


def plain(value):
    """JSON-safe cell values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return "<binary>"
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return str(value)[:2000]


class PostgresSource:
    def __init__(self, config, credentials):
        self.config, self.credentials = config, credentials
        self.port = int(config.get("port") or 5432)
        if not 1 <= self.port <= 65535:
            raise ServiceError("Enter a valid database port.", 422)
        self.host = validate_host(
            config.get("host", ""),
            self.port,
            allow_private=bool(config.get("allow_private_network")),
        )
        if not config.get("database"):
            raise ServiceError("Enter the database name.", 422)

    def _connect(self):
        try:
            conn = psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.config["database"],
                user=self.credentials["username"],
                password=self.credentials["password"],
                sslmode=self.config.get("sslmode") or "prefer",
                connect_timeout=10,
                application_name="optimus-reader",
            )
        except psycopg.OperationalError as exc:
            raise ProviderError(
                "The database could not be reached or rejected the sign-in."
            ) from exc
        conn.read_only = True  # Every transaction begins READ ONLY.
        return conn

    def _run(self, work):
        with self._connect() as conn:
            try:
                with conn.transaction():
                    conn.execute("SET LOCAL statement_timeout = '15s'")
                    return work(conn)
            except psycopg.errors.QueryCanceled as exc:
                raise ProviderError(
                    "The database query took too long. Narrow the request."
                ) from exc
            except psycopg.Error as exc:
                raise ProviderError(
                    f"The database rejected the query ({type(exc).__name__})."
                ) from exc

    async def _call(self, work):
        return await asyncio.to_thread(self._run, work)

    @staticmethod
    def _readable_tables(conn):
        return [
            {"id": f"{schema}.{name}", "name": f"{schema}.{name}", "kind": kind}
            for schema, name, kind in conn.execute(
                "SELECT table_schema, table_name, CASE table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END "
                "FROM information_schema.tables WHERE table_schema <> ALL(%s) "
                "AND table_schema NOT LIKE 'pg_toast%%' "
                "AND has_table_privilege(quote_ident(table_schema) || '.' || quote_ident(table_name), 'SELECT') "
                "ORDER BY 1, 2 LIMIT 500",
                (list(SYSTEM_SCHEMAS),),
            )
        ]

    async def resources(self):
        return {"tables": await self._call(self._readable_tables)}

    def _allowed(self, table):
        if table not in self.config.get("tables", []):
            raise ServiceError("This table is not shared with this workspace.", 403)
        schema, _, name = table.partition(".")
        if not schema or not name:
            raise ServiceError("Use a schema-qualified table name.", 422)
        return schema, name

    @staticmethod
    def _columns(conn, schema, name):
        return {
            column: data_type
            for column, data_type in conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (schema, name),
            )
        }

    async def describe(self, table):
        schema, name = self._allowed(table)

        def work(conn):
            columns = self._columns(conn, schema, name)
            if not columns:
                raise ServiceError("This table no longer exists or is not readable.", 404)
            estimate = conn.execute(
                "SELECT reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname = %s",
                (schema, name),
            ).fetchone()
            cursor = conn.execute(
                sql.SQL("SELECT * FROM {} LIMIT 5").format(sql.Identifier(schema, name))
            )
            headers = [d.name for d in cursor.description]
            return {
                "table": table,
                "columns": [{"name": k, "type": v} for k, v in columns.items()],
                "approximate_rows": max(int(estimate[0]), 0) if estimate else None,
                "sample": [dict(zip(headers, map(plain, row))) for row in cursor.fetchall()],
            }

        return await self._call(work)

    async def query(
        self,
        table,
        *,
        columns=None,
        filters=None,
        order_by=None,
        descending=False,
        limit=100,
        aggregate=None,
        group_by=None,
    ):
        schema, name = self._allowed(table)
        limit = max(1, min(int(limit), MAX_ROWS))

        def work(conn):
            known = self._columns(conn, schema, name)
            if not known:
                raise ServiceError("This table no longer exists or is not readable.", 404)

            def column(value):
                if value not in known:
                    raise ServiceError(f"Unknown column: {str(value)[:80]}", 422)
                return sql.Identifier(value)

            where, params = [], []
            for item in filters or []:
                op = OPERATORS.get(item.get("op", "="))
                if op is None:
                    raise ServiceError("Unsupported filter operator.", 422)
                if item.get("value") is None and op in {"=", "<>"}:
                    where.append(
                        sql.SQL("{} IS {}NULL").format(
                            column(item["column"]), sql.SQL("NOT " if op == "<>" else "")
                        )
                    )
                    continue
                value = item.get("value")
                if op == "ILIKE":
                    value = (
                        "%"
                        + str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                        + "%"
                    )
                    expr = sql.SQL("{}::text ILIKE %s").format(column(item["column"]))
                else:
                    expr = sql.SQL("{} " + op + " %s").format(column(item["column"]))
                where.append(expr)
                params.append(value)
            where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where) if where else sql.SQL("")
            source = sql.Identifier(schema, name)
            if aggregate:
                fn = aggregate.get("function")
                if fn not in AGGREGATES:
                    raise ServiceError("Unsupported aggregate.", 422)
                target = (
                    sql.SQL("*")
                    if fn == "count" and not aggregate.get("column")
                    else column(aggregate.get("column"))
                )
                groups = [column(g) for g in (group_by or [])[:5]]
                select_list = sql.SQL(", ").join(
                    [*groups, sql.SQL(fn + "({}) AS value").format(target)]
                )
                statement = sql.SQL("SELECT {} FROM {}{}").format(select_list, source, where_sql)
                if groups:
                    statement += sql.SQL(" GROUP BY {} ORDER BY value DESC").format(
                        sql.SQL(", ").join(groups)
                    )
            else:
                chosen = [column(c) for c in (columns or [])[:40]] or [sql.SQL("*")]
                statement = sql.SQL("SELECT {} FROM {}{}").format(
                    sql.SQL(", ").join(chosen), source, where_sql
                )
                if order_by:
                    statement += sql.SQL(" ORDER BY {} {}").format(
                        column(order_by), sql.SQL("DESC" if descending else "ASC")
                    )
            statement += sql.SQL(" LIMIT {}").format(sql.Literal(limit + 1))
            cursor = conn.execute(statement, params)
            headers = [d.name for d in cursor.description]
            rows = [dict(zip(headers, map(plain, row))) for row in cursor.fetchall()]
            return {
                "table": table,
                "rows": rows[:limit],
                "truncated": len(rows) > limit,
                "row_limit": limit,
            }

        return await self._call(work)
