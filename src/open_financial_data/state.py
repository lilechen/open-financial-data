"""SQLite operational state for local OFD projects."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from datetime import UTC, datetime, timedelta

from .observability import utc_now


SCHEMA_VERSION = 5

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    config_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    event TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_events(event, occurred_at);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    market TEXT NOT NULL,
    frequency TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    asset_count INTEGER NOT NULL,
    min_event_date TEXT,
    max_event_date TEXT,
    observed_watermark TEXT,
    committed_watermark TEXT,
    size_bytes INTEGER NOT NULL,
    last_run_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, market, frequency, adjustment, provider)
);

CREATE TABLE IF NOT EXISTS dataset_partitions (
    dataset_id TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    min_event_date TEXT NOT NULL,
    max_event_date TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    provider TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY(dataset_id, partition_key),
    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
);

CREATE TABLE IF NOT EXISTS quality_checks (
    quality_check_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL,
    passed_rules INTEGER NOT NULL,
    failed_rules INTEGER NOT NULL,
    warning_rules INTEGER NOT NULL,
    report_path TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
);

CREATE TABLE IF NOT EXISTS run_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    requested_start TEXT NOT NULL,
    requested_end TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    rows_received INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(run_id, asset_id, requested_start, requested_end),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_run_tasks_status ON run_tasks(run_id, status);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    labels_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(name, occurred_at);
"""


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(DDL)
            rows = connection.execute("SELECT version FROM schema_meta").fetchall()
            if not rows:
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif len(rows) == 1 and rows[0]["version"] < SCHEMA_VERSION:
                connection.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))
            elif len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
                raise RuntimeError("Unsupported OFD state schema version")

    def create_run(
        self, *, run_id: str, correlation_id: str, command: str, started_at: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO runs(run_id, correlation_id, command, status, started_at)
                   VALUES (?, ?, ?, 'running', ?)""",
                (run_id, correlation_id, command, started_at),
            )

    def complete_run(self, *, run_id: str, status: str, result: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE runs SET status = ?, finished_at = ?, result_json = ?
                   WHERE run_id = ?""",
                (status, utc_now(), json.dumps(result, sort_keys=True), run_id),
            )

    def create_project(
        self,
        *,
        project_id: str,
        name: str,
        root_path: str,
        config_hash: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO projects(project_id, name, root_path, created_at, config_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (project_id, name, root_path, utc_now(), config_hash),
            )

    def audit(
        self,
        *,
        event: str,
        correlation_id: str,
        run_id: str,
        actor_type: str,
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO audit_events(
                       occurred_at, event, correlation_id, run_id, actor_type, payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    utc_now(),
                    event,
                    correlation_id,
                    run_id,
                    actor_type,
                    json.dumps(payload, sort_keys=True),
                ),
            )

    def summary(self) -> dict[str, int]:
        with self.connect() as connection:
            projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            runs = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            audits = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            datasets = connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            partitions = connection.execute("SELECT COUNT(*) FROM dataset_partitions").fetchone()[0]
            quality_checks = connection.execute("SELECT COUNT(*) FROM quality_checks").fetchone()[0]
            tasks = connection.execute("SELECT COUNT(*) FROM run_tasks").fetchone()[0]
            metrics = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        return {
            "projects": projects,
            "runs": runs,
            "audit_events": audits,
            "datasets": datasets,
            "partitions": partitions,
            "quality_checks": quality_checks,
            "tasks": tasks,
            "metrics": metrics,
        }

    def run_context(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT run_id, correlation_id, command, status FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Run not found: {run_id}")
        return dict(row)

    def record_metric(
        self, *, run_id: str, name: str, value: float, labels: dict[str, str]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO metrics(occurred_at, run_id, name, value, labels_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (utc_now(), run_id, name, value, json.dumps(labels, sort_keys=True)),
            )

    def metric_totals(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT name, labels_json, COUNT(*) AS samples, SUM(value) AS total,
                          MAX(occurred_at) AS last_seen
                   FROM metrics GROUP BY name, labels_json ORDER BY name, labels_json"""
            ).fetchall()
        return [
            {**dict(row), "labels": json.loads(str(row["labels_json"]))}
            for row in rows
        ]

    def search_audit(
        self, *, run_id: str | None = None, event: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event is not None:
            clauses.append("event = ?")
            params.append(event)
        query = "SELECT id, occurred_at, event, correlation_id, run_id, actor_type, payload_json FROM audit_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {**dict(row), "payload": json.loads(str(row["payload_json"]))}
            for row in rows
        ]

    def create_tasks(self, tasks: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO run_tasks(
                       task_id, run_id, asset_id, requested_start, requested_end, status
                   ) VALUES (
                       :task_id, :run_id, :asset_id, :requested_start, :requested_end, 'pending'
                   )""",
                tasks,
            )

    def update_task(
        self,
        *,
        task_id: str,
        status: str,
        attempts: int,
        rows_received: int = 0,
        error: Exception | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """UPDATE run_tasks SET status=?, attempts=?, rows_received=?,
                       error_type=?, error_message=?,
                       started_at=COALESCE(started_at, ?),
                       finished_at=CASE WHEN ? IN ('succeeded','empty','failed') THEN ? ELSE NULL END
                   WHERE task_id=?""",
                (
                    status,
                    attempts,
                    rows_received,
                    type(error).__name__ if error else None,
                    str(error)[:1000] if error else None,
                    now,
                    status,
                    now,
                    task_id,
                ),
            )

    def task_summary(self, run_id: str | None = None) -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS count FROM run_tasks"
        params: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " GROUP BY status"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT run_id, correlation_id, command, status, started_at, finished_at
                   FROM runs ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def tasks_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT task_id, asset_id, requested_start, requested_end, status,
                          attempts, rows_received, error_type, error_message, started_at, finished_at
                   FROM run_tasks WHERE run_id = ? ORDER BY asset_id""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recoverable_tasks(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT task_id, asset_id, requested_start, requested_end, status,
                          attempts, rows_received, error_type, error_message
                   FROM run_tasks
                   WHERE run_id = ? AND status IN (
                       'pending','running','retrying','failed','fetched','interrupted'
                   )
                   ORDER BY asset_id""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_recoverable_run(self) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT t.run_id
                   FROM run_tasks t JOIN runs r ON r.run_id = t.run_id
                   WHERE t.status IN (
                       'pending','running','retrying','failed','fetched','interrupted'
                   )
                   ORDER BY r.started_at DESC LIMIT 1"""
            ).fetchone()
        return str(row["run_id"]) if row is not None else None

    def reconcile_orphaned_runs(
        self, *, active_run_ids: set[str], grace_seconds: int = 60
    ) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(seconds=grace_seconds)
        interrupted: list[str] = []
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT run_id, started_at FROM runs WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                if run_id in active_run_ids:
                    continue
                started = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
                if started > cutoff:
                    continue
                interrupted.append(run_id)
                payload = json.dumps({"status": "interrupted", "reason": "orphaned_run"})
                connection.execute(
                    """UPDATE runs SET status='interrupted', finished_at=?, result_json=?
                       WHERE run_id=?""",
                    (utc_now(), payload, run_id),
                )
                connection.execute(
                    """UPDATE run_tasks SET status='interrupted', finished_at=?
                       WHERE run_id=? AND status IN ('pending','running','retrying')""",
                    (utc_now(), run_id),
                )
        return interrupted

    def register_dataset_import(
        self,
        *,
        dataset: dict[str, Any],
        partitions: list[dict[str, Any]],
    ) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                """SELECT dataset_id FROM datasets
                   WHERE name = :name AND market = :market AND frequency = :frequency
                     AND adjustment = :adjustment AND provider = :provider""",
                dataset,
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "DELETE FROM dataset_partitions WHERE dataset_id = ?",
                    (existing["dataset_id"],),
                )
                connection.execute("DELETE FROM datasets WHERE dataset_id = ?", (existing["dataset_id"],))
            connection.execute(
                """INSERT INTO datasets(
                       dataset_id, name, schema_version, market, frequency, adjustment,
                       provider, status, row_count, asset_count, min_event_date,
                       max_event_date, observed_watermark, committed_watermark,
                       size_bytes, last_run_id, updated_at
                   ) VALUES (
                       :dataset_id, :name, :schema_version, :market, :frequency, :adjustment,
                       :provider, :status, :row_count, :asset_count, :min_event_date,
                       :max_event_date, :observed_watermark, :committed_watermark,
                       :size_bytes, :last_run_id, :updated_at
                   )""",
                dataset,
            )
            connection.executemany(
                """INSERT INTO dataset_partitions(
                       dataset_id, partition_key, row_count, size_bytes, min_event_date,
                       max_event_date, file_count, checksum, provider, run_id
                   ) VALUES (
                       :dataset_id, :partition_key, :row_count, :size_bytes, :min_event_date,
                       :max_event_date, :file_count, :checksum, :provider, :run_id
                   )""",
                partitions,
            )

    def replace_dataset_snapshot(
        self,
        *,
        dataset: dict[str, Any],
        partitions: list[dict[str, Any]],
    ) -> str:
        """Atomically replace Catalog facts while preserving an existing dataset identity."""

        with self.connect() as connection:
            existing = connection.execute(
                """SELECT dataset_id FROM datasets
                   WHERE name = :name AND market = :market AND frequency = :frequency
                     AND adjustment = :adjustment""",
                dataset,
            ).fetchone()
            dataset_id = str(existing["dataset_id"]) if existing is not None else str(dataset["dataset_id"])
            values = {**dataset, "dataset_id": dataset_id}
            if existing is None:
                connection.execute(
                    """INSERT INTO datasets(
                           dataset_id, name, schema_version, market, frequency, adjustment,
                           provider, status, row_count, asset_count, min_event_date,
                           max_event_date, observed_watermark, committed_watermark,
                           size_bytes, last_run_id, updated_at
                       ) VALUES (
                           :dataset_id, :name, :schema_version, :market, :frequency, :adjustment,
                           :provider, :status, :row_count, :asset_count, :min_event_date,
                           :max_event_date, :observed_watermark, :committed_watermark,
                           :size_bytes, :last_run_id, :updated_at
                       )""",
                    values,
                )
            else:
                connection.execute(
                    """UPDATE datasets SET
                           schema_version=:schema_version, provider=:provider,
                           status=:status, row_count=:row_count,
                           asset_count=:asset_count, min_event_date=:min_event_date,
                           max_event_date=:max_event_date, observed_watermark=:observed_watermark,
                           committed_watermark=:committed_watermark, size_bytes=:size_bytes,
                           last_run_id=:last_run_id, updated_at=:updated_at
                       WHERE dataset_id=:dataset_id""",
                    values,
                )
                connection.execute(
                    "DELETE FROM dataset_partitions WHERE dataset_id = ?", (dataset_id,)
                )
            connection.executemany(
                """INSERT INTO dataset_partitions(
                       dataset_id, partition_key, row_count, size_bytes, min_event_date,
                       max_event_date, file_count, checksum, provider, run_id
                   ) VALUES (
                       :dataset_id, :partition_key, :row_count, :size_bytes, :min_event_date,
                       :max_event_date, :file_count, :checksum, :provider, :run_id
                   )""",
                [{**partition, "dataset_id": dataset_id} for partition in partitions],
            )
        return dataset_id

    def dataset_by_name(self, name: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM datasets WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        return dict(row) if row is not None else None

    def dataset_by_identity(
        self, *, name: str, market: str, frequency: str, adjustment: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM datasets WHERE name = ? AND market = ?
                   AND frequency = ? AND adjustment = ? ORDER BY updated_at DESC LIMIT 1""",
                (name, market, frequency, adjustment),
            ).fetchone()
        return dict(row) if row is not None else None

    def dataset_statuses(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            datasets = connection.execute(
                """SELECT dataset_id, name, schema_version, market, frequency, adjustment,
                          provider, status, row_count, asset_count, min_event_date,
                          max_event_date, observed_watermark, committed_watermark,
                          size_bytes, last_run_id, updated_at
                   FROM datasets ORDER BY name, market, frequency"""
            ).fetchall()
            coverage = connection.execute(
                """SELECT dataset_id, provider, COUNT(*) AS partition_count,
                          MIN(min_event_date) AS min_event_date,
                          MAX(max_event_date) AS max_event_date,
                          SUM(row_count) AS row_count
                   FROM dataset_partitions GROUP BY dataset_id, provider"""
            ).fetchall()
        by_dataset: dict[str, list[dict[str, Any]]] = {}
        for row in coverage:
            by_dataset.setdefault(str(row["dataset_id"]), []).append(dict(row))
        return [
            {**dict(row), "provider_coverage": by_dataset.get(str(row["dataset_id"]), [])}
            for row in datasets
        ]

    def partitions_for_dataset(self, dataset_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dataset_partitions WHERE dataset_id = ? ORDER BY partition_key",
                (dataset_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_quality_check(self, values: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO quality_checks(
                       quality_check_id, run_id, dataset_id, profile_id, status,
                       passed_rules, failed_rules, warning_rules, report_path, checked_at
                   ) VALUES (
                       :quality_check_id, :run_id, :dataset_id, :profile_id, :status,
                       :passed_rules, :failed_rules, :warning_rules, :report_path, :checked_at
                   )""",
                values,
            )
            dataset_status = "healthy" if values["status"] == "healthy" else "degraded"
            connection.execute(
                "UPDATE datasets SET status = ?, updated_at = ? WHERE dataset_id = ?",
                (dataset_status, values["checked_at"], values["dataset_id"]),
            )

    def quality_summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            dataset_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM datasets GROUP BY status"
            ).fetchall()
            latest = connection.execute(
                """SELECT quality_check_id, dataset_id, profile_id, status, passed_rules,
                          failed_rules, warning_rules, report_path, checked_at
                   FROM quality_checks ORDER BY checked_at DESC LIMIT 1"""
            ).fetchone()
        return {
            "dataset_status_counts": {row["status"]: row["count"] for row in dataset_rows},
            "latest_quality_check": dict(latest) if latest is not None else None,
        }
