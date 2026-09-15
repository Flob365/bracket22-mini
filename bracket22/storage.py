"""Append-only persistence with serialized business transactions."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, select, text
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
        with self.transaction() as conn:
            metadata.create_all(conn)
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
        previous = conn.execute(select(events).order_by(events.c.id.desc()).limit(1)).mappings().first()
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

    def all_reports(self, conn):
        return [json.loads(r) for r in conn.execute(select(reports.c.payload)).scalars()]

    def report(self, conn, report_id):
        raw = conn.execute(select(reports.c.payload).where(reports.c.id == report_id)).scalar_one_or_none()
        return json.loads(raw) if raw else None

    def verify(self):
        with self.engine.connect() as conn:
            records = list(conn.execute(select(events).order_by(events.c.id)).mappings())
        previous = "0" * 64
        for i, row in enumerate(records, 1):
            record = dict(row)
            digest = record.pop("hash")
            if (
                record["id"] != i
                or record["previous_hash"] != previous
                or hashlib.sha256(canonical(record).encode()).hexdigest() != digest
            ):
                return {"valid": False, "count": len(records), "broken_at": i}
            previous = digest
        return {"valid": True, "count": len(records), "head": previous}
