from contextlib import closing
from datetime import datetime, UTC
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from app import config
from app.indexing.chunking import Chunk


# Pinecone only keeps vectors. BM25, the symbol lookup and the citations all need
# the actual text, so every indexed snapshot is also saved here, keyed by namespace
SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    namespace TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    default_branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    excluded TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    namespace TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (namespace, path)
);
CREATE TABLE IF NOT EXISTS chunks (
    namespace TEXT NOT NULL,
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    language TEXT NOT NULL,
    symbol TEXT,
    kind TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (namespace, id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_key TEXT NOT NULL,
    repository TEXT NOT NULL,
    namespace TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_repository ON jobs (user_key, repository, created_at);
"""

# queued --> downloading --> chunking --> embedding --> uploading --> ready (or failed)
ACTIVE_STATUSES = ("queued", "downloading", "chunking", "embedding", "uploading")


def now() -> str:
    return datetime.now(UTC).isoformat()


class IndexStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self.connect()) as connection, connection:
            # WAL lets status polls keep reading while a big snapshot is being written
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    # A new connection per operation: SQLite connections can't be shared between
    # the threads that asyncio.to_thread() uses
    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    # ---------------------- Snapshots, files and chunks -----------------------
    def save_snapshot(self, namespace: str, snapshot: Any, chunks: list[Chunk]) -> None:
        # One transaction: either the whole snapshot is saved or none of it is
        with closing(self.connect()) as connection, connection:
            self._delete_namespace(connection, namespace)
            connection.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                (namespace, snapshot.owner, snapshot.name, snapshot.default_branch,
                 snapshot.commit_sha, json.dumps(snapshot.excluded), now()),
            )
            connection.executemany(
                "INSERT INTO files VALUES (?, ?, ?)",
                [(namespace, path, content) for path, content in snapshot.files.items()],
            )
            connection.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(namespace, chunk.id, chunk.path, chunk.start_line, chunk.end_line, chunk.language,
                  chunk.symbol, chunk.kind, chunk.commit_sha, chunk.text) for chunk in chunks],
            )

    def get_files(self, namespace: str) -> dict[str, str]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT path, content FROM files WHERE namespace = ?", (namespace,))
            return {row["path"]: row["content"] for row in rows}

    def get_chunks(self, namespace: str) -> list[Chunk]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM chunks WHERE namespace = ? ORDER BY path, start_line, end_line", (namespace,)
            )
            return [Chunk(**{key: row[key] for key in row.keys() if key != "namespace"}) for row in rows]

    def delete_namespace(self, namespace: str) -> None:
        with closing(self.connect()) as connection, connection:
            self._delete_namespace(connection, namespace)

    def delete_other_commits(self, prefix: str, keep_namespace: str) -> None:
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                "SELECT namespace FROM snapshots WHERE namespace LIKE ? ESCAPE '\\' AND namespace != ?",
                (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", keep_namespace),
            ).fetchall()
            for row in rows:
                self._delete_namespace(connection, row["namespace"])

    def _delete_namespace(self, connection: sqlite3.Connection, namespace: str) -> None:
        for table in ("snapshots", "files", "chunks"):
            connection.execute(f"DELETE FROM {table} WHERE namespace = ?", (namespace,))

    # ---------------------- Jobs -----------------------------------------------
    # Creates a job unless this user already has one running for this repository, or
    # this exact commit is already indexed. Returns (job, created).
    # BEGIN IMMEDIATE locks the database for writing before the check, so two requests
    # (even from two server processes) can never both create a job.
    # Only one job per user + repository can run at a time, because a finished job
    # deletes the other commits and would otherwise delete a job that is still running
    def create_job_unless_busy(
        self, user_key: str, repository: str, namespace: str, commit_sha: str
    ) -> tuple[dict[str, Any], bool]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._get_latest_job(connection, user_key, repository)
            is_busy = latest is not None and latest["status"] in ACTIVE_STATUSES
            is_done = latest is not None and latest["namespace"] == namespace and latest["status"] == "ready"
            if is_busy or is_done:
                connection.rollback()
                return latest, False

            job_id = uuid4().hex
            timestamp = now()
            connection.execute(
                "INSERT INTO jobs (id, user_key, repository, namespace, commit_sha, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (job_id, user_key, repository.lower(), namespace, commit_sha, timestamp, timestamp),
            )
            connection.commit()
        finally:
            connection.close()

        return self.get_job(job_id), True

    # A job only cleans up other commits if it is still the newest job for its
    # repository, so a slow old job can never delete a newer index
    def is_latest_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        latest = job and self.get_latest_job(job["user_key"], job["repository"])
        return bool(latest and latest["id"] == job_id)

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "progress", "total", "error"}
        if not fields.keys() <= allowed:
            raise ValueError(f"Unknown job fields: {fields.keys() - allowed}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), now(), job_id),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    # The latest job of this user for this repository, whatever commit it was for
    def get_latest_job(self, user_key: str, repository: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            return self._get_latest_job(connection, user_key, repository)

    # The newest commit that finished indexing. Questions are always answered from this one,
    # even while a newer commit is being indexed or after a newer job failed
    def get_latest_ready_job(self, user_key: str, repository: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE user_key = ? AND repository = ? AND status = 'ready'"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (user_key, repository.lower()),
            ).fetchone()
            return dict(row) if row else None

    def _get_latest_job(self, connection: sqlite3.Connection, user_key: str, repository: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM jobs WHERE user_key = ? AND repository = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_key, repository.lower()),
        ).fetchone()
        return dict(row) if row else None

    # Jobs run inside the server process, so a restart stops them half way.
    # They are marked as failed on startup instead of showing "embedding" forever.
    # This assumes one server process: with several workers, one worker starting up
    # would also fail jobs that another worker is still running
    def fail_interrupted_jobs(self) -> int:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                f"UPDATE jobs SET status = 'failed', error = 'The server restarted while indexing', updated_at = ?"
                f" WHERE status IN ({placeholders})",
                (now(), *ACTIVE_STATUSES),
            )
            return cursor.rowcount


_store: IndexStore | None = None


def get_store() -> IndexStore:
    global _store
    if _store is None:
        _store = IndexStore(config.INDEX_DB_PATH)
    return _store
