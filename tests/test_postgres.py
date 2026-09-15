"""Run the same lifecycle contract against an isolated PostgreSQL schema."""

import os
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_app import (
    test_api_daily_idempotent_and_no_automatic_orders,
    test_close_paper_position,
    test_concurrent_approval_cannot_exceed_limits,
    test_human_approval_recheck_and_duplicate,
    test_immutable_database_and_restart,
    test_matured_returns_no_lookahead_and_idempotence,
    test_stale_approval_rejected,
)

from bracket22.app import create_app

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL for PostgreSQL integration"
)


@pytest.fixture
def desk():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    engine = create_engine(url)
    schema = "test_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    app = create_app(scoped, today=lambda: date(2026, 9, 11))
    try:
        with TestClient(app) as client:
            yield client, app.state.service
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_postgres_truncate_forbidden(desk):
    c, s = desk
    c.post("/analysis/SPY")
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), s.store.engine.begin() as conn:
        conn.execute(text("TRUNCATE events"))


__all__ = [
    "test_api_daily_idempotent_and_no_automatic_orders",
    "test_close_paper_position",
    "test_concurrent_approval_cannot_exceed_limits",
    "test_human_approval_recheck_and_duplicate",
    "test_immutable_database_and_restart",
    "test_matured_returns_no_lookahead_and_idempotence",
    "test_stale_approval_rejected",
]
