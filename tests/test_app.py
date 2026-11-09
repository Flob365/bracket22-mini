import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_app_available():
    assert importlib.util.find_spec("bracket22.app") is not None, "Missing API and paper lifecycle"


@pytest.fixture
def desk(tmp_path):
    from bracket22.app import create_app

    app = create_app(f"sqlite:///{tmp_path}/test.db", today=lambda: date(2026, 9, 11))
    with TestClient(app) as client:
        yield client, app.state.service


def test_api_daily_idempotent_and_no_automatic_orders(desk):
    c, _s = desk
    assert len(c.get("/assets").json()) == 20
    assert c.get("/market/NOPE").status_code == 404
    assert c.get("/reports/SPY").status_code == 404
    first = c.post("/analysis/run-daily")
    assert first.status_code == 200, first.text
    assert len(first.json()["reports"]) == 20
    second = c.post("/analysis/run-daily").json()
    assert [r["id"] for r in first.json()["reports"]] == [r["id"] for r in second["reports"]]
    assert c.get("/portfolio").json()["cash_eur"] == 100000
    assert c.get("/portfolio").json()["positions"] == []
    for route in [
        "/market/SPY",
        "/reports/latest",
        "/reports/SPY",
        "/opportunities",
        "/risks",
        "/performance",
        "/agents/performance",
        "/journal",
        "/",
    ]:
        assert c.get(route).status_code == 200, route
    assert c.post("/orders").status_code == 404
    assert c.get("/journal").json()["valid"] is True


def test_human_approval_recheck_and_duplicate(desk):
    c, _s = desk
    c.post("/analysis/run-daily")
    eligible = c.get("/opportunities").json()
    assert len(eligible) >= 4
    report = eligible[0]
    response = c.post(f"/proposals/{report['id']}/review", json={"action": "approve"})
    assert response.status_code == 200, response.text
    p = c.get("/portfolio").json()
    assert len(p["positions"]) == 1
    assert p["cash_eur"] == pytest.approx(95000)
    assert p["equity_eur"] == pytest.approx(100000)
    assert c.post(f"/proposals/{report['id']}/review", json={"action": "approve"}).status_code == 409
    successes = 1
    for r in eligible[1:]:
        res = c.post(f"/proposals/{r['id']}/review", json={"action": "approve"})
        successes += res.status_code == 200
    assert successes == 3
    assert len(c.get("/portfolio").json()["positions"]) == 3
    assert c.get("/journal").json()["valid"]


def test_immutable_database_and_restart(desk):
    c, s = desk
    r = c.post("/analysis/SPY").json()
    for statement in [
        "UPDATE events SET kind='tampered'",
        "DELETE FROM events",
        "UPDATE reports SET payload='{}'",
    ]:
        with pytest.raises(DBAPIError), s.store.engine.begin() as conn:
            conn.execute(text(statement))
    assert c.get("/reports/SPY").json()["id"] == r["id"]
    from bracket22.app import create_app

    with TestClient(
        create_app(s.store.engine.url.render_as_string(hide_password=False), today=s.today)
    ) as again:
        assert again.get("/reports/SPY").json()["id"] == r["id"]
        assert again.get("/journal").json()["valid"]


def test_matured_returns_no_lookahead_and_idempotence(desk):
    c, s = desk
    r = c.post("/analysis/SPY").json()
    assert c.get("/performance").json()["observations"] == []
    s.today = lambda: date(2026, 12, 15)
    c.post("/performance/refresh")
    values = c.get("/performance").json()["observations"]
    assert {v["horizon"] for v in values} == {1, 5, 20, 60}
    c.post("/performance/refresh")
    assert c.get("/performance").json()["observations"] == values
    bars = s.provider.history("SPY", date(2026, 12, 15))
    next_bar = next(b for b in bars if b.day > date(2026, 9, 11))
    one = next(v for v in values if v["horizon"] == 1)
    assert one["return"] == pytest.approx(next_bar.close / r["reference_price"] - 1)
    stats = c.get("/agents/performance").json()
    assert stats["Houston"]["horizons"]["20"]["count"] == 1


def test_stale_approval_rejected(desk):
    c, s = desk
    c.post("/analysis/run-daily")
    r = c.get("/opportunities").json()[0]
    s.today = lambda: date(2026, 9, 20)
    response = c.post(f"/proposals/{r['id']}/review", json={"action": "approve"})
    assert response.status_code == 409
    assert c.get("/portfolio").json()["positions"] == []


def test_concurrent_approval_cannot_exceed_limits(desk):
    c, _s = desk
    c.post("/analysis/run-daily")
    reports = c.get("/opportunities").json()
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(
            pool.map(lambda r: c.post(f"/proposals/{r['id']}/review", json={"action": "approve"}), reports)
        )
    assert sum(r.status_code == 200 for r in responses) == 3
    assert len(c.get("/portfolio").json()["positions"]) == 3
    assert c.get("/journal").json()["valid"]


def test_close_paper_position(desk):
    c, _s = desk
    c.post("/analysis/run-daily")
    r = c.get("/opportunities").json()[0]
    c.post(f"/proposals/{r['id']}/review", json={"action": "approve"})
    assert c.post(f"/portfolio/{r['symbol']}/close").status_code == 200
    assert c.get("/portfolio").json()["positions"] == []
    assert c.get("/portfolio").json()["cash_eur"] == pytest.approx(100000)
    assert c.post(f"/portfolio/{r['symbol']}/close").status_code == 409


def test_dashboard_single_call_matches_individual_endpoints(desk):
    c, s = desk
    c.post("/analysis/run-daily")
    report = c.get("/opportunities").json()[0]
    c.post(f"/proposals/{report['id']}/review", json={"action": "approve"})
    d = c.get("/dashboard").json()
    assert d["reports"] == c.get("/reports/latest").json()
    assert d["portfolio"] == c.get("/portfolio").json()
    assert d["agents"] == c.get("/agents/performance").json()
    assert d["operations"] == c.get("/operations").json()
    assert d["assets"] == c.get("/assets").json()
    full = c.get("/journal").json()
    assert d["journal"]["valid"] and d["journal"]["count"] == full["count"]
    assert d["journal"]["head"] == full["head"]
    assert [e["kind"] for e in d["journal"]["events"]] == [e["kind"] for e in full["events"][::-1][:12]]
    assert d["journal"]["events"][0] == {
        "id": full["count"], "kind": "PAPER_BUY", "at": full["events"][-1]["at"], "label": report["symbol"]
    }
    assert all("market_snapshot" not in r and not any(k.startswith("_") for k in r) for r in d["reports"])
    assert len(d["reports"][0]["sparkline"]) == 60
    assert [e["id"] for e in c.get("/journal?limit=3").json()["events"]] == [e["id"] for e in full["events"][-3:]]
    # Cached summaries are rebuilt identically by a fresh process.
    from bracket22.service import Service

    again = Service(s.store.engine.url.render_as_string(hide_password=False), today=s.today)
    assert again.latest() == s.latest()
    again.store.engine.dispose()


def test_incremental_verification_follows_new_events_and_detects_tampering(desk):
    c, s = desk
    c.post("/analysis/SPY")
    first = s.store.verify(incremental=True)
    c.post("/analysis/QQQ")
    second = s.store.verify(incremental=True)
    assert second["valid"] and second["count"] > first["count"]
    assert second == s.store.verify()
    with s.store.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER immutable_events_UPDATE"))
        conn.execute(text("UPDATE events SET hash = 'forged' WHERE id = :id"), {"id": second["count"]})
    assert s.store.verify(incremental=True)["valid"] is False
    assert c.get("/dashboard").json()["journal"]["valid"] is False


def test_static_assets_revalidate_and_api_is_compressed(desk):
    c, _s = desk
    c.post("/analysis/run-daily")
    page = c.get("/static/app.js")
    assert page.headers["cache-control"] == "no-cache"
    assert c.get("/static/app.js", headers={"If-None-Match": page.headers["etag"]}).status_code == 304
    data = c.get("/dashboard", headers={"Accept-Encoding": "gzip"})
    assert data.headers["cache-control"] == "no-store"
    assert data.headers["content-encoding"] == "gzip"
