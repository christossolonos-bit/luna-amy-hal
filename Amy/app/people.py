"""Who he talks to. People are keyed by a stable platform identity (a Discord user ID), not by the
name they happen to show, so a nickname change doesn't turn someone into a stranger. Bots are
flagged, and someone only counts as a person he has talked with once he has actually replied to them;
voices he merely overheard are remembered separately."""

import time


class People:
    def __init__(self, repo) -> None:
        self.repo = repo
        repo.conn.execute("""CREATE TABLE IF NOT EXISTS people(
            key TEXT PRIMARY KEY,            -- 'discord:<user id>', or 'name:<display name>' when no id is known
            name TEXT NOT NULL,              -- the name he knows them by (first seen); facts hang off this
            display TEXT NOT NULL,           -- what they're called right now
            is_bot INTEGER NOT NULL DEFAULT 0,
            messages INTEGER NOT NULL DEFAULT 0,
            replied INTEGER NOT NULL DEFAULT 0,
            overheard INTEGER NOT NULL DEFAULT 0,
            first_seen REAL NOT NULL, last_seen REAL NOT NULL)""")
        self._backfill()

    def _backfill(self) -> None:
        """Earlier messages were logged by display name only: give each of those a record once."""
        if self.repo.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]:
            return
        rows = self.repo.conn.execute("""
            SELECT u.speaker, COUNT(DISTINCT u.id) AS messages, COUNT(DISTINCT t.utterance_id) AS replied,
                   MIN(u.created_at) AS first, MAX(u.created_at) AS last
            FROM utterances u LEFT JOIN dialogue_turns t ON t.utterance_id = u.id AND t.role = 'engine'
            GROUP BY u.speaker""").fetchall()
        now = time.time()
        with self.repo.conn:
            for r in rows:
                self.repo.conn.execute(
                    "INSERT OR IGNORE INTO people(key, name, display, messages, replied, overheard, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"name:{r['speaker']}", r["speaker"], r["speaker"], r["messages"], r["replied"],
                     r["messages"] - r["replied"], now, now))

    def identify(self, display: str, user_id: str | None = None, is_bot: bool = False) -> str:
        """The name he knows this person by. A known ID with a new display name keeps the old name
        (and gains the new one as an alias), so their facts and history stay theirs."""
        key = f"discord:{user_id}" if user_id else f"name:{display}"
        now = time.time()
        row = self.repo.conn.execute("SELECT * FROM people WHERE key = ?", (key,)).fetchone()
        if row is None and user_id:
            # first time we see their ID: adopt a legacy name-only record of the same name, if any
            legacy = self.repo.conn.execute("SELECT * FROM people WHERE key = ?", (f"name:{display}",)).fetchone()
            with self.repo.conn:
                if legacy:
                    self.repo.conn.execute("UPDATE people SET key = ?, is_bot = ? WHERE key = ?",
                                           (key, int(is_bot), legacy["key"]))
                else:
                    self.repo.conn.execute(
                        "INSERT INTO people(key, name, display, is_bot, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                        (key, display, display, int(is_bot), now, now))
            row = self.repo.conn.execute("SELECT * FROM people WHERE key = ?", (key,)).fetchone()
        elif row is None:
            with self.repo.conn:
                self.repo.conn.execute(
                    "INSERT INTO people(key, name, display, is_bot, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                    (key, display, display, int(is_bot), now, now))
            row = self.repo.conn.execute("SELECT * FROM people WHERE key = ?", (key,)).fetchone()
        if row["display"] != display:
            entity = self.repo.find_entity(row["name"])
            if entity is not None and self.repo.find_entity(display) in (None, entity):
                self.repo.add_alias(entity, display)
            with self.repo.conn:
                self.repo.conn.execute("UPDATE people SET display = ? WHERE key = ?", (display, key))
        if is_bot and not row["is_bot"]:
            with self.repo.conn:
                self.repo.conn.execute("UPDATE people SET is_bot = 1 WHERE key = ?", (key,))
        self.current_key = key
        return row["name"]

    def heard(self, key: str, addressed: bool) -> None:
        column = "messages" if addressed else "overheard"
        with self.repo.conn:
            self.repo.conn.execute(f"UPDATE people SET {column} = {column} + 1, last_seen = ? WHERE key = ?",
                                   (time.time(), key))

    def replied(self, key: str) -> None:
        with self.repo.conn:
            self.repo.conn.execute("UPDATE people SET replied = replied + 1 WHERE key = ?", (key,))

    def record(self, name: str):
        return self.repo.conn.execute("SELECT * FROM people WHERE name = ? OR display = ? ORDER BY replied DESC LIMIT 1",
                                      (name, name)).fetchone()

    def talked_with(self, bots: bool = False) -> list:
        """People he has actually replied to, most recent first."""
        return self.repo.conn.execute(
            "SELECT * FROM people WHERE replied > 0 AND is_bot = ? ORDER BY last_seen DESC", (int(bots),)).fetchall()

    def count_people(self) -> int:
        return len(self.talked_with())
