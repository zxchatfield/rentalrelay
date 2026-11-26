"""Tests for database.py: local sqlite3 by default, remote Turso (libSQL)
when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are both set. Remote selection
is tested without network (stubbed libsql.connect); the wrapper itself is
tested against the real libsql client on local/in-memory databases."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import libsql

import database


def test_local_sqlite_when_turso_vars_absent(monkeypatch, tmp_path):
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    assert database.is_remote() is False
    assert database.turso_config() is None
    path = tmp_path / 'local.db'
    conn = database.connect(str(path))
    assert isinstance(conn, sqlite3.Connection)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)')
    conn.execute("INSERT INTO t(name) VALUES ('x')")
    conn.commit()
    row = conn.execute('SELECT id, name FROM t').fetchone()
    assert row['name'] == 'x'
    assert dict(row) == {'id': 1, 'name': 'x'}
    conn.close()


def test_partial_turso_config_stays_local(monkeypatch):
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://example.turso.io')
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    assert database.is_remote() is False
    conn = database.connect(':memory:')
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_remote_selected_when_both_vars_set(monkeypatch):
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://example.turso.io')
    monkeypatch.setenv('TURSO_AUTH_TOKEN', 'fake-token-for-test-only')
    assert database.is_remote() is True
    calls = {}

    class FakeLibsql:
        @staticmethod
        def connect(url, auth_token='', timeout=5.0):
            calls['url'] = url
            calls['auth_token'] = auth_token
            return object()  # _Connection only stores it; no methods called

    monkeypatch.setitem(sys.modules, 'libsql', FakeLibsql)
    conn = database.connect(':memory:')
    assert isinstance(conn, database._Connection)
    assert calls == {'url': 'libsql://example.turso.io',
                     'auth_token': 'fake-token-for-test-only'}


def test_remote_row_dict_access_and_mapping():
    conn = database._Connection(libsql.connect(':memory:'))
    conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT, v REAL)')
    conn.execute("INSERT INTO t(name,v) VALUES ('x',1.5)")
    conn.execute("INSERT INTO t(name,v) VALUES ('y',2.5)")
    row = conn.execute('SELECT id, name, v FROM t ORDER BY id').fetchone()
    assert row['name'] == 'x'
    assert row[0] == 1
    assert row['v'] == 1.5
    assert dict(row) == {'id': 1, 'name': 'x', 'v': 1.5}
    assert row.keys() == ['id', 'name', 'v']
    rows = conn.execute('SELECT name FROM t ORDER BY id').fetchall()
    assert [r[0] for r in rows] == ['x', 'y']
    conn.close()


def test_remote_executescript():
    conn = database._Connection(libsql.connect(':memory:'))
    conn.executescript("CREATE TABLE a(x TEXT); CREATE TABLE b(y TEXT); INSERT INTO a VALUES ('s');")
    assert conn.execute('SELECT x FROM a').fetchone()['x'] == 's'
    conn.close()


def test_remote_executescript_raises_on_bad_statement():
    conn = database._Connection(libsql.connect(':memory:'))
    try:
        conn.executescript("CREATE TABLE a(x TEXT); BAD SYNTAX HERE;")
        raised = False
    except ValueError:
        raised = True
    assert raised, 'a bad statement inside executescript must raise like sqlite3'
    conn.close()


def test_remote_executescript_keeps_semicolon_in_string():
    conn = database._Connection(libsql.connect(':memory:'))
    conn.executescript("CREATE TABLE a(x TEXT); INSERT INTO a VALUES ('has;semicolon');")
    assert conn.execute('SELECT x FROM a').fetchone()['x'] == 'has;semicolon'
    conn.close()


def test_remote_rowcount_and_lastrowid():
    conn = database._Connection(libsql.connect(':memory:'))
    conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)')
    cur = conn.execute("INSERT INTO t(name) VALUES ('a'), ('b')")
    assert cur.rowcount == 2
    assert cur.lastrowid == 2
    cur = conn.execute("UPDATE t SET name='z' WHERE name='a'")
    assert cur.rowcount == 1
    conn.close()


def test_remote_context_manager_commits_and_rolls_back(tmp_path):
    path = str(tmp_path / 'remote.db')
    conn = database._Connection(libsql.connect(path))
    conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)')
    with conn:
        conn.execute("INSERT INTO t(name) VALUES ('committed')")
    conn.close()
    check = libsql.connect(path)
    assert check.execute("SELECT name FROM t").fetchone()[0] == 'committed'
    check.close()
    conn = database._Connection(libsql.connect(path))
    try:
        with conn:
            conn.execute("INSERT INTO t(name) VALUES ('rolled-back')")
            raise RuntimeError('boom')
    except RuntimeError:
        pass
    conn.close()
    check = libsql.connect(path)
    assert check.execute("SELECT count(*) FROM t WHERE name='rolled-back'").fetchone()[0] == 0
    assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    check.close()


def test_local_uri_read_only(tmp_path):
    path = tmp_path / 'u.db'
    conn = database.connect(str(path))
    conn.execute('CREATE TABLE t(name TEXT)')
    conn.execute("INSERT INTO t VALUES ('x')")
    conn.commit()
    conn.close()
    uri = path.resolve().as_uri() + '?mode=ro'
    ro = database.connect(uri, uri=True, timeout=5)
    assert ro.execute('SELECT name FROM t').fetchone()[0] == 'x'
    ro.close()
