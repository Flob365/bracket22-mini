import importlib.util
from datetime import date, timedelta

import pytest


def test_core_available():
    assert importlib.util.find_spec("bracket22.core") is not None, "Missing deterministic domain engine"


def test_provider_reproducible_without_future_leakage():
    from bracket22.core import SampleProvider

    p = SampleProvider()
    a = p.history("SPY", date(2026, 9, 1))
    b = p.history("SPY", date(2026, 9, 10))
    assert a == [bar for bar in b if bar.day <= date(2026, 9, 1)]
    assert max(bar.day for bar in a) <= date(2026, 9, 1)


def test_flat_indicators_and_invalid_data():
    from bracket22.core import Bar, DataEngine

    bars = [
        Bar(day=date(2025, 1, 1) + timedelta(days=i), open=100, high=100, low=100, close=100, volume=1000)
        for i in range(260)
    ]
    result = DataEngine().calculate(bars, bars)
    assert result["rsi14"] == 50
    assert result["atr14"] == 0
    assert result["momentum20"] == 0
    assert result["volatility"] == 0
    assert result["max_drawdown"] == 0
    assert result["sharpe"] == 0
    with pytest.raises(ValueError):
        DataEngine().calculate(bars[:10], bars)
    with pytest.raises(ValueError):
        DataEngine().calculate(bars + [bars[-1]], bars)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"weight": 0.051}, "position_limit"),
        ({"sector_weight": 0.21}, "sector_limit"),
        ({"crypto": True, "crypto_weight": 0.06}, "crypto_limit"),
        ({"new_today": 3}, "daily_limit"),
        ({"score": 6.9}, "score_below_minimum"),
        ({"confidence": 0.59}, "confidence_below_minimum"),
        ({"missing": True}, "missing_data"),
        ({"stale": True}, "stale_data"),
        ({"weight": float("nan")}, "invalid_numbers"),
        ({"weight": -1}, "invalid_numbers"),
    ],
)
def test_risk_rejects(change, reason):
    from bracket22.core import RiskEngine

    args = {
        "weight": 0.05,
        "sector_weight": 0,
        "crypto_weight": 0,
        "crypto": False,
        "new_today": 0,
        "score": 7,
        "confidence": 0.6,
        "missing": False,
        "stale": False,
    }
    args.update(change)
    assert reason in RiskEngine().evaluate(**args)["reasons"]


def test_risk_exact_boundaries():
    from bracket22.core import RiskEngine

    assert (
        RiskEngine().evaluate(
            weight=0.05,
            sector_weight=0.20,
            crypto_weight=0.05,
            crypto=True,
            new_today=2,
            score=7,
            confidence=0.6,
            missing=False,
            stale=False,
        )["status"]
        == "APPROVED"
    )
