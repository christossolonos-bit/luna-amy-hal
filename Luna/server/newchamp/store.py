"""SQLite store for New Champ–style per-user memories (MiniLM embeddings)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_db_path: Optional[Path] = None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / denom)


def configure(db_path: Path) -> None:
    global _conn, _db_path
    with _lock:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if _conn is not None and _db_path == path:
            return
        if _conn is not None:
            _conn.close()
        _db_path = path
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
              id INTEGER PRIMARY KEY,
              user_id TEXT NOT NULL,
              content TEXT NOT NULL,
              embedding TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS memories_user ON memories (user_id);
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY,
              conversation TEXT NOT NULL,
              role TEXT NOT NULL,
              user_id TEXT,
              name TEXT,
              content TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_conv ON messages (conversation, id);
            """
        )
        _conn.commit()
        print(f"[newchamp] store ready db={path}")


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("[newchamp] store not configured")
    return _conn


def _embed(texts: list[str]) -> list[np.ndarray]:
    from server.memory.embedder import get_embedder

    model = get_embedder()
    vecs = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return [np.asarray(v, dtype=np.float32) for v in vecs]


def user_memories(user_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _db().execute(
            "SELECT id, content, embedding FROM memories WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "content": r["content"],
            "embedding": np.asarray(json.loads(r["embedding"]), dtype=np.float32),
        })
    return out


def recall(
    user_id: str,
    query: str,
    *,
    top_k: int = 6,
    min_score: float = 0.35,
) -> list[dict[str, Any]]:
    rows = user_memories(user_id)
    if not rows or not (query or "").strip():
        return []
    qv = _embed([query.strip()])[0]
    scored = []
    for r in rows:
        score = _cosine(qv, r["embedding"])
        if score >= min_score:
            scored.append({"id": r["id"], "content": r["content"], "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def remember(
    user_id: str,
    facts: list[str],
    *,
    dedupe_score: float = 0.9,
) -> list[str]:
    clean = [f.strip() for f in facts if isinstance(f, str) and f.strip()]
    if not clean:
        return []
    existing = [r["embedding"] for r in user_memories(user_id)]
    vectors = _embed(clean)
    saved: list[str] = []
    with _lock:
        db = _db()
        for fact, vec in zip(clean, vectors):
            if any(_cosine(vec, e) >= dedupe_score for e in existing):
                continue
            db.execute(
                "INSERT INTO memories (user_id, content, embedding, created_at) VALUES (?, ?, ?, ?)",
                (user_id, fact, json.dumps(vec.tolist()), int(time.time() * 1000)),
            )
            existing.append(vec)
            saved.append(fact)
        db.commit()
    return saved


def add_message(
    conversation: str,
    *,
    role: str,
    content: str,
    user_id: str | None = None,
    name: str | None = None,
) -> None:
    with _lock:
        _db().execute(
            "INSERT INTO messages (conversation, role, user_id, name, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation, role, user_id, name, content, int(time.time() * 1000)),
        )
        _db().commit()


def import_text_memories_from_companion(companion_db: Path) -> int:
    """Copy fact strings from new champ companion.db and re-embed with MiniLM."""
    src = Path(companion_db)
    if not src.is_file():
        return 0
    imported = 0
    with sqlite3.connect(str(src)) as other:
        other.row_factory = sqlite3.Row
        try:
            rows = other.execute(
                "SELECT user_id, content FROM memories ORDER BY id"
            ).fetchall()
        except sqlite3.Error:
            return 0
    by_user: dict[str, list[str]] = {}
    for r in rows:
        by_user.setdefault(str(r["user_id"]), []).append(str(r["content"]))
    for uid, facts in by_user.items():
        saved = remember(uid, facts)
        imported += len(saved)
        if saved:
            print(f"[newchamp] imported {len(saved)} memories for user={uid}")
    return imported
