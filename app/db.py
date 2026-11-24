"""MySQL access: connection pool, query helpers and transactions.

* One pooled connection is borrowed per request/app-context and returned
  automatically when the context ends.
* Connections run in autocommit mode. Use ``with transaction():`` for
  anything that must succeed or fail as a unit.
* Every helper takes parameters separately from the SQL text (``%s``
  placeholders), so queries are always parameterised.
"""
import threading
from collections import namedtuple
from contextlib import contextmanager

from flask import current_app, g
from mysql.connector import errors as mysql_errors
from mysql.connector import pooling

_pools = {}
_pool_lock = threading.Lock()

ExecResult = namedtuple("ExecResult", ["rowcount", "lastrowid"])

DUPLICATE_KEY_ERRNO = 1062


def _get_pool():
    cfg = current_app.config
    key = (cfg["DB_HOST"], cfg["DB_PORT"], cfg["DB_USER"], cfg["DB_NAME"])
    with _pool_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = pooling.MySQLConnectionPool(
                pool_name=f"nexora_pool_{len(_pools)}",
                pool_size=cfg["DB_POOL_SIZE"],
                pool_reset_session=True,
                host=cfg["DB_HOST"],
                port=cfg["DB_PORT"],
                user=cfg["DB_USER"],
                password=cfg["DB_PASSWORD"],
                database=cfg["DB_NAME"],
                charset="utf8mb4",
                collation="utf8mb4_0900_ai_ci",
                autocommit=True,
                connection_timeout=10,
            )
            _pools[key] = pool
        return pool


def get_db():
    """Return this context's connection, borrowing one from the pool."""
    if "db" not in g:
        g.db = _get_pool().get_connection()
        g.db_depth = 0
    return g.db


def close_db(exc=None):
    """Return the connection to the pool (an open transaction is rolled back)."""
    conn = g.pop("db", None)
    g.pop("db_depth", None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def query_all(sql, params=None):
    cur = get_db().cursor(dictionary=True, buffered=True)
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        cur.close()


def query_one(sql, params=None):
    cur = get_db().cursor(dictionary=True, buffered=True)
    try:
        cur.execute(sql, params or ())
        return cur.fetchone()
    finally:
        cur.close()


def execute(sql, params=None):
    """Run INSERT/UPDATE/DELETE. Returns ExecResult(rowcount, lastrowid)."""
    cur = get_db().cursor(buffered=True)
    try:
        cur.execute(sql, params or ())
        return ExecResult(cur.rowcount, cur.lastrowid)
    finally:
        cur.close()


@contextmanager
def transaction():
    """Commit on success, roll back on any exception.

    Nested use joins the outer transaction; only the outermost block
    commits or rolls back.
    """
    conn = get_db()
    depth = g.get("db_depth", 0)
    if depth == 0:
        conn.start_transaction()
    g.db_depth = depth + 1
    try:
        yield conn
    except BaseException:
        g.db_depth = depth
        if depth == 0:
            conn.rollback()
        raise
    else:
        g.db_depth = depth
        if depth == 0:
            conn.commit()


def is_duplicate_key(exc, key_name=None):
    """True if exc is a MySQL duplicate-key error (optionally for a named key)."""
    if getattr(exc, "errno", None) != DUPLICATE_KEY_ERRNO:
        return False
    return key_name is None or key_name in str(exc)


IntegrityError = mysql_errors.IntegrityError
DatabaseError = mysql_errors.Error
