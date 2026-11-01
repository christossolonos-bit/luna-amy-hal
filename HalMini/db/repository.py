"""All SQL lives here. Terms are ('e', entity_id) or ('c', concept_id)."""

import json
import sqlite3
from typing import Any

from db.migrations import migrate
from reasoning.rules import RULES

Term = tuple[str, int]
ArgRecord = tuple[str, str, int | None, str | None]  # role, value_type ('e'|'c'|'lit'), value_id, literal
NONE_CONCEPT = "_"  # object slot of intransitive relations ("Howard lives in Ohio")


class Repository:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        migrate(self.conn)
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO rules(name, description) VALUES (?, ?)", RULES.items()
            )

    def close(self) -> None:
        self.conn.close()

    # --- lexicon and concepts -------------------------------------------------

    def note_lexeme(self, lemma: str, pos: str, form: str | None = None) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO lexemes(lemma, pos) VALUES (?, ?)", (lemma, pos))
            if form and form != lemma:
                lex_id = self.conn.execute(
                    "SELECT id FROM lexemes WHERE lemma = ? AND pos = ?", (lemma, pos)
                ).fetchone()[0]
                self.conn.execute(
                    "INSERT OR IGNORE INTO inflections(lexeme_id, form) VALUES (?, ?)", (lex_id, form)
                )

    def learned_verbs(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT lemma FROM lexemes WHERE pos = 'verb'")}

    def find_concept(self, lemma: str) -> int | None:
        row = self.conn.execute("SELECT id FROM concepts WHERE lemma = ?", (lemma,)).fetchone()
        return row[0] if row else None

    def concept(self, lemma: str) -> int:
        existing = self.find_concept(lemma)
        if existing is not None:
            return existing
        with self.conn:
            return self.conn.execute("INSERT INTO concepts(lemma) VALUES (?)", (lemma,)).lastrowid

    def none_concept(self) -> int:
        return self.concept(NONE_CONCEPT)

    def concept_lemma(self, concept_id: int) -> str:
        return self.conn.execute("SELECT lemma FROM concepts WHERE id = ?", (concept_id,)).fetchone()[0]

    # --- entities ---------------------------------------------------------------

    def find_entity(self, name: str) -> int | None:
        row = self.conn.execute("SELECT entity_id FROM aliases WHERE alias = ?", (name,)).fetchone()
        return row[0] if row else None

    def entity(self, name: str) -> int:
        existing = self.find_entity(name)
        if existing is not None:
            return existing
        with self.conn:
            eid = self.conn.execute(
                "INSERT INTO entities(name, proper) VALUES (?, 1)", (name,)
            ).lastrowid
            self.conn.execute("INSERT INTO aliases(entity_id, alias) VALUES (?, ?)", (eid, name))
        return eid

    def add_alias(self, entity_id: int, alias: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO aliases(entity_id, alias) VALUES (?, ?)", (entity_id, alias))

    def new_instance(self, kind_concept_id: int) -> int:
        lemma = self.concept_lemma(kind_concept_id)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE kind_concept_id = ?", (kind_concept_id,)
        ).fetchone()[0]
        with self.conn:
            return self.conn.execute(
                "INSERT INTO entities(name, proper, kind_concept_id) VALUES (?, 0, ?)",
                (f"{lemma}#{count + 1}", kind_concept_id),
            ).lastrowid

    def entity_row(self, entity_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()

    def instances_of(self, kind_concept_id: int) -> list[int]:
        return [r[0] for r in self.conn.execute(
            "SELECT id FROM entities WHERE kind_concept_id = ? AND proper = 0 ORDER BY id", (kind_concept_id,)
        )]

    def properties(self, entity_id: int) -> list[str]:
        return [self.concept_lemma(r["obj_id"])
                for r in self.assertions("has_property", subj=("e", entity_id), polarity=True)]

    def related_instance(self, subj: Term, predicate: str, kind: int, mods: tuple[str, ...]) -> int | None:
        """An unnamed thing of this kind and exactly these properties already linked to subj."""
        for row in self.assertions(predicate, subj=subj, polarity=True):
            if row["obj_type"] != "e":
                continue
            ent = self.entity_row(row["obj_id"])
            if not ent["proper"] and ent["kind_concept_id"] == kind and set(self.properties(ent["id"])) == set(mods):
                return ent["id"]
        return None

    # --- assertions -------------------------------------------------------------

    def add_assertion(
        self, subj: Term, predicate: str, obj: Term, polarity: bool,
        source_utterance_id: int | None, confidence: float = 1.0,
        tense: str | None = None, args: list[ArgRecord] | tuple = (),
    ) -> int:
        """Insert a claim, or return the identical one already held (raising its confidence if
        restated more firmly). Opposite claims are kept side by side, never overwritten."""
        key = (subj[0], subj[1], predicate, obj[0], obj[1], int(polarity))
        wanted = sorted((r, t, i, lit) for r, t, i, lit in args)
        for row in self.conn.execute(
            "SELECT id, confidence FROM assertions WHERE subj_type=? AND subj_id=? AND predicate=? "
            "AND obj_type=? AND obj_id=? AND polarity=? AND status='active'", key,
        ).fetchall():
            if self.arg_records(row["id"]) == wanted:
                if confidence > row["confidence"]:
                    with self.conn:
                        self.conn.execute("UPDATE assertions SET confidence = ? WHERE id = ?",
                                          (confidence, row["id"]))
                return row["id"]
        with self.conn:
            aid = self.conn.execute(
                "INSERT INTO assertions(subj_type, subj_id, predicate, obj_type, obj_id, polarity, "
                "confidence, temporal_scope, source_utterance_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*key, confidence, tense, source_utterance_id),
            ).lastrowid
            self.conn.executemany(
                "INSERT INTO assertion_args(assertion_id, role, value_type, value_id, literal) "
                "VALUES (?, ?, ?, ?, ?)", [(aid, *a) for a in args],
            )
        return aid

    def arg_records(self, assertion_id: int) -> list[ArgRecord]:
        return sorted(tuple(r) for r in self.conn.execute(
            "SELECT role, value_type, value_id, literal FROM assertion_args WHERE assertion_id = ?",
            (assertion_id,),
        ))

    def args(self, assertion_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM assertion_args WHERE assertion_id = ? ORDER BY id", (assertion_id,)
        ).fetchall()

    def assertions(
        self, predicate: str, subj: Term | None = None, obj: Term | None = None,
        polarity: bool | None = None,
    ) -> list[sqlite3.Row]:
        """`predicate` ending in '%' matches by prefix, e.g. 'rel:%'."""
        op = "LIKE" if predicate.endswith("%") else "="
        sql = f"SELECT * FROM assertions WHERE predicate {op} ? AND status = 'active'"
        params: list[Any] = [predicate]
        if subj is not None:
            sql += " AND subj_type = ? AND subj_id = ?"
            params += list(subj)
        if obj is not None:
            sql += " AND obj_type = ? AND obj_id = ?"
            params += list(obj)
        if polarity is not None:
            sql += " AND polarity = ?"
            params.append(int(polarity))
        return self.conn.execute(sql + " ORDER BY id", params).fetchall()

    def assertion(self, assertion_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM assertions WHERE id = ?", (assertion_id,)).fetchone()

    def all_assertions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM assertions WHERE status = 'active' ORDER BY id").fetchall()

    def source_speaker(self, assertion_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT u.speaker FROM assertions a JOIN utterances u ON u.id = a.source_utterance_id "
            "WHERE a.id = ?", (assertion_id,),
        ).fetchone()
        return row[0] if row else None

    def subject_entities(self) -> list[Term]:
        return [("e", r[0]) for r in self.conn.execute(
            "SELECT DISTINCT subj_id FROM assertions WHERE subj_type = 'e' AND status = 'active' ORDER BY subj_id")]

    def retract(self, assertion_id: int, utterance_id: int | None, note: str) -> None:
        """Retire a claim. It stays in the database (with the correction that retired it) for audit."""
        with self.conn:
            self.conn.execute("UPDATE assertions SET status = 'retracted' WHERE id = ?", (assertion_id,))
            self.conn.execute("INSERT INTO corrections(assertion_id, utterance_id, note) VALUES (?, ?, ?)",
                              (assertion_id, utterance_id, note))

    # --- learned if-then rules --------------------------------------------------

    def add_rule(self, conditions: list, conclusions: list, variable: dict, confidence: float,
                 source_utterance_id: int | None) -> int:
        cj, kj, vj = json.dumps(conditions), json.dumps(conclusions), json.dumps(variable)
        row = self.conn.execute(
            "SELECT id, confidence FROM learned_rules WHERE conditions_json = ? AND conclusions_json = ? "
            "AND status = 'active'", (cj, kj)).fetchone()
        if row:
            if confidence > row["confidence"]:
                with self.conn:
                    self.conn.execute("UPDATE learned_rules SET confidence = ? WHERE id = ?", (confidence, row["id"]))
            return row["id"]
        with self.conn:
            return self.conn.execute(
                "INSERT INTO learned_rules(conditions_json, conclusions_json, variable_json, confidence, "
                "source_utterance_id) VALUES (?, ?, ?, ?, ?)", (cj, kj, vj, confidence, source_utterance_id),
            ).lastrowid

    @staticmethod
    def _rule_dict(row) -> dict:
        return {"id": row["id"], "conditions": json.loads(row["conditions_json"]),
                "conclusions": json.loads(row["conclusions_json"]),
                "variable": json.loads(row["variable_json"]), "confidence": row["confidence"],
                "status": row["status"]}

    def rule(self, rule_id: int) -> dict:
        return self._rule_dict(self.conn.execute("SELECT * FROM learned_rules WHERE id = ?", (rule_id,)).fetchone())

    def active_rules(self) -> list[dict]:
        return [self._rule_dict(r) for r in self.conn.execute(
            "SELECT * FROM learned_rules WHERE status = 'active' ORDER BY id")]

    def retract_rule(self, rule_id: int, utterance_id: int | None, note: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE learned_rules SET status = 'retracted' WHERE id = ?", (rule_id,))
            self.conn.execute("INSERT INTO corrections(rule_id, utterance_id, note) VALUES (?, ?, ?)",
                              (rule_id, utterance_id, note))

    # --- dialogue records -------------------------------------------------------

    def log_utterance(self, speaker: str, text: str) -> int:
        with self.conn:
            uid = self.conn.execute(
                "INSERT INTO utterances(speaker, text) VALUES (?, ?)", (speaker, text)
            ).lastrowid
            self.conn.execute(
                "INSERT INTO dialogue_turns(utterance_id, role, text) VALUES (?, 'user', ?)", (uid, text)
            )
        return uid

    def turns(self, before_utterance: int | None = None) -> list[sqlite3.Row]:
        """Conversation history, newest first: role ('user'|'engine'), speaker, text, created_at (UTC)."""
        sql = ("SELECT t.id, t.utterance_id, t.role, t.text, t.created_at, u.speaker, u.text AS prompt FROM dialogue_turns t "
               "JOIN utterances u ON u.id = t.utterance_id")
        params: list[Any] = []
        if before_utterance is not None:
            sql += " WHERE t.utterance_id < ?"
            params.append(before_utterance)
        return self.conn.execute(sql + " ORDER BY t.id DESC", params).fetchall()

    def source_utterance(self, assertion_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT u.* FROM assertions a JOIN utterances u ON u.id = a.source_utterance_id WHERE a.id = ?",
            (assertion_id,)).fetchone()

    def log_reply(self, utterance_id: int, text: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO dialogue_turns(utterance_id, role, text) VALUES (?, 'engine', ?)",
                (utterance_id, text),
            )

    def save_trace(self, utterance_id: int, status: str | None, trace: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO inference_traces(utterance_id, status, trace_json) VALUES (?, ?, ?)",
                (utterance_id, status, json.dumps(trace, default=str)),
            )

    def open_question(self, utterance_id: int, frame: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO questions(utterance_id, frame_json) VALUES (?, ?)",
                (utterance_id, json.dumps(frame, default=str)),
            )

    def open_questions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM questions WHERE status = 'open' ORDER BY id").fetchall()
