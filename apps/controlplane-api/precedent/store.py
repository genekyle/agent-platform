"""The vector store — sqlite-vec beside the corpus it indexes (PLAN_inhouse_reasoner_v1 §3).

One file (`vectors.db`), two tables: `precedents` (metadata, one row per situation) and the
`vec0` virtual table `precedent_vec` whose rowid IS the precedent id. Backfill is idempotent
on `source_key`. The same schema ports to pgvector when the image swap is approved — nothing
here assumes sqlite beyond the connection line.
"""
from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Optional

from .embedder import DIM, PrecedentDoc

SCHEMA_VERSION = 1


def _serialize(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def deserialize(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class VectorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS precedents (
                id INTEGER PRIMARY KEY,
                source_key TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,
                session TEXT, ts TEXT,
                platform TEXT, ats TEXT, state TEXT, phase TEXT, task TEXT,
                intent TEXT, ref TEXT, verdict TEXT, teacher_label TEXT,
                has_vision INTEGER NOT NULL DEFAULT 0,
                screenshot TEXT, artifact TEXT,
                text TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_precedents_kind ON precedents(kind);
            CREATE INDEX IF NOT EXISTS idx_precedents_session ON precedents(session);
            CREATE TABLE IF NOT EXISTS store_info (key TEXT PRIMARY KEY, value TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS precedent_vec USING vec0(
                embedding float[{DIM}]
            );
            """
        )
        self.db.execute(
            "INSERT OR REPLACE INTO store_info(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()

    def set_info(self, key: str, value) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO store_info(key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        self.db.commit()

    def has(self, source_key: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM precedents WHERE source_key = ?", (source_key,)
        ).fetchone()
        return row is not None

    def add(self, doc: PrecedentDoc, vector: list[float], has_vision: bool) -> Optional[int]:
        """Insert one precedent; returns its id, or None if source_key already stored."""
        if self.has(doc.source_key):
            return None
        cur = self.db.execute(
            """
            INSERT INTO precedents (source_key, kind, session, ts, platform, ats, state, phase,
                                    task, intent, ref, verdict, teacher_label, has_vision,
                                    screenshot, artifact, text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.source_key, doc.kind, doc.session, doc.ts, doc.platform, doc.ats,
                doc.state, doc.phase, doc.task, doc.intent, doc.ref, doc.verdict,
                doc.teacher_label, 1 if has_vision else 0,
                str(doc.screenshot) if doc.screenshot else "", doc.artifact, doc.text,
            ),
        )
        pid = cur.lastrowid
        self.db.execute(
            "INSERT INTO precedent_vec(rowid, embedding) VALUES (?, ?)",
            (pid, _serialize(vector)),
        )
        return pid

    def commit(self) -> None:
        self.db.commit()

    def knn(self, vector: list[float], k: int = 15, kinds: Optional[list[str]] = None,
            exclude_sessions: Optional[set[str]] = None) -> list[dict]:
        """Nearest precedents. Metadata filters run AFTER the vector match (over-fetch then
        filter), which is exact enough at this corpus size; a partitioned index can come later."""
        fetch = k * 8 if (kinds or exclude_sessions) else k
        rows = self.db.execute(
            """
            SELECT v.rowid, v.distance, p.kind, p.session, p.platform, p.ats, p.state, p.phase,
                   p.intent, p.ref, p.verdict, p.teacher_label, p.has_vision, p.text
            FROM precedent_vec v JOIN precedents p ON p.id = v.rowid
            WHERE v.embedding MATCH ? AND v.k = ?
            ORDER BY v.distance
            """,
            (_serialize(vector), fetch),
        ).fetchall()
        out = []
        for row in rows:
            rec = {
                "id": row[0], "distance": row[1], "kind": row[2], "session": row[3],
                "platform": row[4], "ats": row[5], "state": row[6], "phase": row[7],
                "intent": row[8], "ref": row[9], "verdict": row[10],
                "teacher_label": row[11], "has_vision": row[12], "text": row[13],
            }
            if kinds and rec["kind"] not in kinds:
                continue
            if exclude_sessions and rec["session"] in exclude_sessions:
                continue
            out.append(rec)
            if len(out) >= k:
                break
        return out

    def all_vectors(self) -> tuple[list[dict], list[list[float]]]:
        """Everything, for offline evaluation (brute force in numpy is exact and instant at
        this corpus size)."""
        metas, vecs = [], []
        for row in self.db.execute(
            """
            SELECT p.id, p.kind, p.session, p.platform, p.ats, p.state, p.phase, p.task,
                   p.intent, p.ref, p.verdict, p.teacher_label, p.has_vision, v.embedding
            FROM precedents p JOIN precedent_vec v ON p.id = v.rowid
            ORDER BY p.id
            """
        ):
            metas.append({
                "id": row[0], "kind": row[1], "session": row[2], "platform": row[3],
                "ats": row[4], "state": row[5], "phase": row[6], "task": row[7],
                "intent": row[8], "ref": row[9], "verdict": row[10],
                "teacher_label": row[11], "has_vision": row[12],
            })
            vecs.append(deserialize(row[13]))
        return metas, vecs

    def counts(self) -> dict:
        out = {}
        for kind, n in self.db.execute("SELECT kind, COUNT(*) FROM precedents GROUP BY kind"):
            out[kind] = n
        out["total"] = sum(out.values())
        out["with_vision"] = self.db.execute(
            "SELECT COUNT(*) FROM precedents WHERE has_vision = 1"
        ).fetchone()[0]
        return out

    def close(self) -> None:
        self.db.commit()
        self.db.close()
