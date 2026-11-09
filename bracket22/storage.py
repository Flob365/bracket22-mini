"""Append-only persistence with serialized business transactions."""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, func, select, text
from sqlalchemy.exc import OperationalError


class StorageBusy(Exception):
    pass


metadata = MetaData()
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(40), nullable=False),
    Column("at", String(40), nullable=False),
    Column("payload", Text, nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)
reports = Table(
    "reports",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("key", String(100), unique=True, nullable=False),
    Column("payload", Text, nullable=False),
)
measurements = Table(
    "measurements",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("payload", Text, nullable=False),
)
schema = Table("schema_version", metadata, Column("version", Integer, primary_key=True))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, url):
        self.engine = create_engine(
            url, connect_args={"timeout": 30} if str(url).startswith("sqlite") else {}
        )
        # Events are append-only: a verified prefix and decoded labels stay valid for the process.
        self._lock = threading.Lock()
        self._checkpoint = None
        self._labels = {}
        with self.transaction() as conn:
            metadata.create_all(conn)
            conn.execute(text("CREATE INDEX IF NOT EXISTS events_kind ON events (kind, id)"))
            if conn.execute(select(schema)).first() is None:
                conn.execute(schema.insert().values(version=1))
            if conn.execute(select(schema.c.version)).scalar_one() != 1:
                raise RuntimeError("Unsupported schema version")
            for table in ("events", "reports", "measurements"):
                if self.engine.dialect.name == "sqlite":
                    for action in ("UPDATE", "DELETE"):
                        conn.execute(
                            text(
                                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable journal'); END"
                            )
                        )
                else:
                    conn.execute(
                        text(
                            "CREATE OR REPLACE FUNCTION bracket_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable journal'; END; $$"
                        )
                    )
                    trigger = f"immutable_{table}"
                    exists = conn.execute(
                        text("SELECT 1 FROM pg_trigger WHERE tgname=:name"), {"name": trigger}
                    ).first()
                    if not exists:
                        conn.execute(
                            text(
                                f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION bracket_immutable()"
                            )
                        )

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            try:
                if self.engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    conn.execute(text("SELECT pg_advisory_xact_lock(220022)"))
                yield conn
                conn.commit()
            except OperationalError as exc:
                conn.rollback()
                if self.engine.dialect.name == "sqlite" and getattr(exc.orig, "sqlite_errorcode", None) in (
                    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED
                ):
                    raise StorageBusy("Un traitement est déjà en cours. Attendez sa fin puis réessayez.") from None
                raise
            except BaseException:
                conn.rollback()
                raise

    def append(self, conn, kind, payload):
        previous = conn.execute(
            select(events.c.id, events.c.hash).order_by(events.c.id.desc()).limit(1)
        ).mappings().first()
        record = {
            "id": previous["id"] + 1 if previous else 1,
            "kind": kind,
            "at": datetime.now(UTC).isoformat(),
            "payload": canonical(payload),
            "previous_hash": previous["hash"] if previous else "0" * 64,
        }
        record["hash"] = hashlib.sha256(canonical(record).encode()).hexdigest()
        conn.execute(events.insert().values(**record))
        return record["id"]

    def all_events(self, conn):
        return [
            dict(r, payload=json.loads(r["payload"]))
            for r in conn.execute(select(events).order_by(events.c.id)).mappings()
        ]

    def events_of(self, conn, *kinds):
        """Events of the given kinds only, without decoding unrelated (large) payloads."""
        query = select(events).where(events.c.kind.in_(kinds)).order_by(events.c.id)
        return [dict(r, payload=json.loads(r["payload"])) for r in conn.execute(query).mappings()]

    def recent_events(self, conn, limit):
        """Compact newest-first view: id, kind, timestamp and a short subject label."""
        head = conn.execute(
            select(events.c.id, events.c.kind, events.c.at).order_by(events.c.id.desc()).limit(limit)
        ).mappings().all()
        missing = [r["id"] for r in head if r["id"] not in self._labels]
        if missing:
            rows = conn.execute(select(events.c.id, events.c.payload).where(events.c.id.in_(missing)))
            for event_id, raw in rows:
                payload = json.loads(raw)
                label = payload.get("symbol") or payload.get("agent") or payload.get("report_id") or ""
                self._labels[event_id] = str(label)
            for stale in sorted(self._labels)[:-500]:
                del self._labels[stale]
        return [dict(r, label=self._labels.get(r["id"], "")) for r in head]

    def all_reports(self, conn):
        return [json.loads(r) for r in conn.execute(select(reports.c.payload)).scalars()]

    def report(self, conn, report_id):
        raw = conn.execute(select(reports.c.payload).where(reports.c.id == report_id)).scalar_one_or_none()
        return json.loads(raw) if raw else None

    def verify(self, incremental=False):
        """Check the hash chain. `incremental` resumes after the prefix this process already verified."""
        with self._lock, self.engine.connect() as conn:
            count, previous = 0, "0" * 64
            checkpoint = self._checkpoint if incremental else None
            if checkpoint and checkpoint[0]:
                anchor = conn.execute(
                    select(events.c.hash).where(events.c.id == checkpoint[0])
                ).scalar_one_or_none()
                if anchor == checkpoint[1]:
                    count, previous = checkpoint
            start = count
            rows = conn.execution_options(yield_per=200).execute(
                select(events).where(events.c.id > start).order_by(events.c.id)
            ).mappings()
            for row in rows:
                record = dict(row)
                digest = record.pop("hash")
                count += 1
                if (
                    record["id"] != count
                    or record["previous_hash"] != previous
                    or hashlib.sha256(canonical(record).encode()).hexdigest() != digest
                ):
                    rows.close()
                    self._checkpoint = None
                    total = conn.execute(select(func.count()).select_from(events)).scalar_one()
                    return {"valid": False, "count": total, "broken_at": count}
                previous = digest
            self._checkpoint = (count, previous)
        return {"valid": True, "count": count, "head": previous}
