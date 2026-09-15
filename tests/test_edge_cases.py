from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from bracket22.app import create_app
from bracket22.core import Bar, DataEngine, SampleProvider


def test_numeric_uptrend_known_momentum():
    bars = [
        Bar(
            day=date(2024, 1, 1) + timedelta(days=i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
            volume=1000,
        )
        for i in range(300)
    ]
    result = DataEngine().calculate(bars, bars)
    assert result["rsi14"] == 100
    assert result["sma50"] == 374.5
    assert result["atr14"] == 2
    assert result["momentum20"] == pytest.approx(20 / 379)
    assert result["relative_strength20"] == 0
    assert result["correlation_spy"] == pytest.approx(1)


@pytest.mark.parametrize("failure", ["missing", "stale", "future", "unsorted"])
def test_bad_provider_cannot_create_approvable_proposal(tmp_path, failure):
    class BadProvider(SampleProvider):
        def history(self, symbol, as_of):
            bars = super().history(symbol, as_of)
            if symbol != "AAPL":
                return bars
            return {
                "missing": [],
                "stale": bars[:-5],
                "future": bars + [bars[-1].model_copy(update={"day": as_of + timedelta(days=1)})],
                "unsorted": list(reversed(bars)),
            }[failure]

    with TestClient(
        create_app(f"sqlite:///{tmp_path}/test.db", provider=BadProvider(), today=lambda: date(2026, 9, 11))
    ) as c:
        r = c.post("/analysis/AAPL").json()
        assert r["risk"]["status"] == "REJECTED"
        assert c.get("/portfolio").json()["positions"] == []


def test_data_disappearing_at_approval_is_logged(tmp_path):
    class Provider(SampleProvider):
        fail = False

        def history(self, symbol, as_of):
            if self.fail:
                raise ValueError("Feed unavailable")
            return super().history(symbol, as_of)

    p = Provider()
    with TestClient(
        create_app(f"sqlite:///{tmp_path}/test.db", provider=p, today=lambda: date(2026, 9, 11))
    ) as c:
        c.post("/analysis/run-daily")
        r = c.get("/opportunities").json()[0]
        p.fail = True
        assert c.post(f"/proposals/{r['id']}/review", json={"action": "approve"}).status_code == 409
        assert c.get("/journal").json()["events"][-1]["kind"] == "RISK_RECHECK_REJECTED"


def test_token_and_invalid_review_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("BRACKET22_API_TOKEN", "local-test-token")
    with TestClient(create_app(f"sqlite:///{tmp_path}/test.db")) as c:
        assert c.get("/").status_code == 200
        assert c.get("/portfolio").status_code == 401
        assert c.post("/analysis/SPY").status_code == 401
        c.headers["Authorization"] = "Bearer local-test-token"
        assert c.get("/portfolio").status_code == 200
        assert c.post("/proposals/anything/review", json={"action": "buy_real"}).status_code == 422
        assert (
            c.post("/proposals/anything/review", json={"action": "approve", "size": 1000000}).status_code
            == 422
        )


def test_reject_prevents_subsequent_approval(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path}/test.db", today=lambda: date(2026, 9, 11))) as c:
        r = c.post("/analysis/SPY").json()
        assert c.post(f"/proposals/{r['id']}/review", json={"action": "reject"}).status_code == 200
        assert c.post(f"/proposals/{r['id']}/review", json={"action": "approve"}).status_code == 409
        assert c.get("/portfolio").json()["cash_eur"] == 100000


def test_internal_missing_session_rejected(tmp_path):
    class GapProvider(SampleProvider):
        def history(self, symbol, as_of):
            bars = super().history(symbol, as_of)
            return bars[:500] + bars[501:] if symbol == "SPY" else bars

    with TestClient(
        create_app(f"sqlite:///{tmp_path}/gap.db", provider=GapProvider(), today=lambda: date(2026, 9, 11))
    ) as c:
        r = c.post("/analysis/SPY").json()
        assert r["risk"]["status"] == "REJECTED"
        assert "missing_data" in r["risk"]["reasons"]


def test_docs_can_load_their_scripts(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path}/docs.db")) as c:
        docs = c.get("/docs")
        policy = docs.headers.get("content-security-policy", "")
        assert "https://cdn.jsdelivr.net" in policy
        assert "'unsafe-inline'" not in c.get("/").headers["content-security-policy"]
