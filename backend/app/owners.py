import sqlite3
import threading
import time
from pathlib import Path


class Owners:
    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS owners (session_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, "
                         "created_at REAL NOT NULL)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(owners)")}
            for name in ("deleted_at REAL", "id_q TEXT"):
                if name.split()[0] not in columns:
                    conn.execute(f"ALTER TABLE owners ADD COLUMN {name}")
            conn.execute("CREATE INDEX IF NOT EXISTS owners_id_q ON owners (id_q)")
            conn.execute("CREATE INDEX IF NOT EXISTS owners_student ON owners (student_id, created_at DESC)")
            conn.execute("CREATE TABLE IF NOT EXISTS students (student_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                         "email TEXT, major TEXT, school_year TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS intake_attempts (intake_key TEXT PRIMARY KEY, "
                         "attempt INTEGER NOT NULL)")
            conn.commit()
            self._conn = conn
        return self._conn

    def add(self, session_id: str, student_id: str, id_q: str | None = None) -> None:
        with self._lock:
            db = self._db()
            db.execute("INSERT OR IGNORE INTO owners (session_id, student_id, created_at, id_q) VALUES (?, ?, ?, ?)",
                       (session_id, student_id, time.time(), id_q))
            db.commit()

    def id_q_of(self, session_id: str) -> str | None:
        with self._lock:
            row = self._db().execute("SELECT id_q FROM owners WHERE session_id = ?", (session_id,)).fetchone()
        return row[0] if row else None

    def intake_owner(self, id_q: str) -> str | None:
        with self._lock:
            row = self._db().execute("SELECT student_id FROM owners WHERE id_q = ? AND deleted_at IS NULL LIMIT 1",
                                     (id_q,)).fetchone()
        return row[0] if row else None

    def owner(self, session_id: str) -> str | None:
        with self._lock:
            row = self._db().execute("SELECT student_id FROM owners WHERE session_id = ? AND deleted_at IS NULL", (session_id,)).fetchone()
        return row[0] if row else None

    def sessions_of(self, student_id: str) -> set[str]:
        with self._lock:
            rows = self._db().execute("SELECT session_id FROM owners WHERE student_id = ? AND deleted_at IS NULL", (student_id,)).fetchall()
        return {row[0] for row in rows}

    def retire(self, session_id: str) -> None:
        with self._lock:
            db = self._db()
            db.execute("UPDATE owners SET deleted_at = ? WHERE session_id = ? AND deleted_at IS NULL",
                       (time.time(), session_id))
            db.commit()

    def created_since(self, student_id: str, since: float) -> int:
        with self._lock:
            row = self._db().execute("SELECT COUNT(*) FROM owners WHERE student_id = ? AND created_at >= ?",
                                     (student_id, since)).fetchone()
        return row[0]

    def intake_attempt(self, intake_key: str) -> int:
        """How many intakes created under this idempotency key were deleted after a failed session create."""
        with self._lock:
            row = self._db().execute("SELECT attempt FROM intake_attempts WHERE intake_key = ?",
                                     (intake_key,)).fetchone()
        return row[0] if row else 0

    def bump_intake_attempt(self, intake_key: str) -> None:
        with self._lock:
            db = self._db()
            db.execute("INSERT INTO intake_attempts (intake_key, attempt) VALUES (?, 1) "
                       "ON CONFLICT(intake_key) DO UPDATE SET attempt = attempt + 1", (intake_key,))
            db.commit()

    # ---- registered students (profile asked once, at the first sign-in) ----
    STUDENT_FIELDS = ("name", "email", "major", "school_year")

    def student(self, student_id: str) -> dict | None:
        with self._lock:
            row = self._db().execute("SELECT student_id, name, email, major, school_year, created_at FROM students "
                                     "WHERE student_id = ?", (student_id,)).fetchone()
        if row is None:
            return None
        return dict(zip(("student_id", *self.STUDENT_FIELDS, "created_at"), row))

    def register_student(self, student_id: str, profile: dict) -> dict:
        now = time.time()
        values = [(str(profile.get(key) or "").strip() or None) for key in self.STUDENT_FIELDS]
        with self._lock:
            db = self._db()
            db.execute("INSERT INTO students (student_id, name, email, major, school_year, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(student_id) DO UPDATE SET name = excluded.name, "
                       "email = excluded.email, major = excluded.major, school_year = excluded.school_year, "
                       "updated_at = excluded.updated_at", (student_id, *values, now, now))
            db.commit()
        return self.student(student_id) or {}

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
