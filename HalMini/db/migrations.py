import sqlite3

from db.schema import MIGRATIONS


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row[0] if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations, each in its own transaction. Returns the new version."""
    version = current_version(conn)
    for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(
            f"BEGIN;\n{sql}\nDELETE FROM schema_version;\n"
            f"INSERT INTO schema_version(version) VALUES ({number});\nCOMMIT;"
        )
        version = number
    return version
