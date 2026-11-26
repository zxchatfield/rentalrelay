"""Database adapter: local sqlite3, or remote Turso (libSQL) when configured.

When the TURSO_DATABASE_URL and TURSO_AUTH_TOKEN environment variables are
both set, connections go to the remote Turso database through the libsql
client; otherwise the existing local sqlite3 file path is used exactly as
before. The remote connection is wrapped so it speaks the interface the app
already uses: dict-style row access (row['col'], dict(row)), executescript,
context-manager commit/rollback, rowcount/lastrowid.

Local behaviour is unchanged when the Turso variables are absent, and this
module never requires libsql to be installed for the local path (it is
imported lazily, only when a remote database is configured).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# libsql surfaces SQL failures as ValueError; sqlite3 uses sqlite3.Error.
# Call sites that used to catch sqlite3.Error should catch ERRORS instead.
ERRORS = (sqlite3.Error, ValueError)


def turso_config():
    """(url, auth_token) when a remote Turso database is configured, else None.

    Both variables must be set; a half-configured remote is treated as local
    so a typo in one variable cannot silently point the app at the wrong DB.
    """
    url = os.getenv('TURSO_DATABASE_URL', '').strip()
    token = os.getenv('TURSO_AUTH_TOKEN', '').strip()
    return (url, token) if url and token else None


def is_remote():
    """True when the app should talk to Turso instead of a local file."""
    return turso_config() is not None


class Row(tuple):
    """A result row supporting positional and column-name access, like
    sqlite3.Row, plus the Mapping protocol (keys() / dict(row))."""
    def __new__(cls, values, columns):
        obj = super().__new__(cls, tuple(values))
        obj._columns = tuple(columns)
        return obj

    def __init__(self, values, columns):
        # __new__ already built the tuple and stored the columns.
        pass

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                index = self._columns.index(key)
            except ValueError:
                raise IndexError(f'no such column: {key!r}') from None
            return super().__getitem__(index)
        return super().__getitem__(key)

    def keys(self):
        return list(self._columns)


def _split_script(script):
    """Split a multi-statement SQL script on semicolons, respecting string
    literals ('...', "...", `...`, [...]) and -- / /* */ comments, so a
    ';' inside a literal never splits a statement."""
    stmts, buf = [], []
    i, n = 0, len(script)
    quote = None  # closing character of the string/identifier we are inside
    while i < n:
        ch = script[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                # '' is an escaped quote inside a '...' string literal.
                if quote == "'" and i + 1 < n and script[i + 1] == "'":
                    buf.append(script[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', '`'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == '[':
            quote = ']'
            buf.append(ch)
            i += 1
            continue
        if ch == '-' and i + 1 < n and script[i + 1] == '-':
            while i < n and script[i] != '\n':
                buf.append(script[i])
                i += 1
            continue
        if ch == '/' and i + 1 < n and script[i + 1] == '*':
            while i < n and not (script[i] == '*' and i + 1 < n and script[i + 1] == '/'):
                buf.append(script[i])
                i += 1
            if i < n:
                buf.append('*/')
                i += 2
            continue
        if ch == ';':
            stmt = ''.join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


class _Cursor:
    """Wraps a libsql cursor so fetched rows behave like sqlite3.Row rows."""
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def arraysize(self):
        return self._cursor.arraysize

    def _wrap(self, row):
        if row is None:
            return None
        columns = [d[0] for d in (self._cursor.description or [])]
        return Row(row, columns)

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]

    def fetchmany(self, size=None):
        if size is None:
            size = self._cursor.arraysize
        return [self._wrap(r) for r in self._cursor.fetchmany(size)]

    def execute(self, sql, params=()):
        return _Cursor(self._cursor.execute(sql, params))

    def executemany(self, sql, seq_of_params):
        return _Cursor(self._cursor.executemany(sql, seq_of_params))

    def executescript(self, sql):
        return self._cursor.executescript(sql)

    def close(self):
        return self._cursor.close()


class _Connection:
    """Wraps a libsql connection with the sqlite3 Connection surface the
    app uses. ``row_factory`` is accepted for compatibility and ignored:
    rows from this connection are always dict-capable."""
    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None

    def execute(self, sql, params=()):
        return _Cursor(self._conn.execute(sql, params))

    def executemany(self, sql, seq_of_params):
        return _Cursor(self._conn.executemany(sql, seq_of_params))

    def executescript(self, sql):
        # libsql's native executescript silently ignores statement errors,
        # so run the statements one by one: a bad statement must raise,
        # exactly like sqlite3's executescript. The caller commits.
        for stmt in _split_script(sql):
            self._conn.execute(stmt)
        return None

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirror sqlite3: commit on a clean exit, roll back on error.
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def connect(path=None, timeout=20, uri=False):
    """Open a database connection.

    Remote (Turso) when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set: the
    local path is ignored. Otherwise a local sqlite3 connection to ``path``
    (a path, a file: URI when ``uri`` is true, or DATABASE_PATH /
    rentalrelay.sqlite3 by default).
    """
    cfg = turso_config()
    if cfg is not None:
        url, token = cfg
        import libsql
        return _Connection(libsql.connect(url, auth_token=token, timeout=timeout))
    if path is None:
        path = os.getenv('DATABASE_PATH', str(ROOT / 'rentalrelay.sqlite3'))
    return sqlite3.connect(path, timeout=timeout, uri=uri)
