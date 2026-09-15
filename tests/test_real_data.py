from datetime import UTC, date, datetime
from importlib.util import find_spec

import pandas as pd
import pytest


def test_real_provider_exists():
    assert find_spec("bracket22.real_data") is not None


def test_completed_sessions_and_holidays(tmp_path):
    from bracket22.real_data import YahooProvider

    p = YahooProvider(tmp_path, now=lambda: datetime(2026, 9, 14, 15, tzinfo=UTC))
    assert p.expected_date("SPY", date(2026, 9, 14)) == date(2026, 9, 11)
    assert p.expected_date("SPY", date(2026, 9, 7)) == date(2026, 9, 4)
    assert p.expected_date("BTC", date(2026, 9, 14)) == date(2026, 9, 13)
    assert p.expected_date("BTC", date(2026, 9, 12)) == date(2026, 9, 12)
    p.now = lambda: datetime(2026, 9, 14, 21, tzinfo=UTC)
    assert p.expected_date("SPY", date(2026, 9, 14)) == date(2026, 9, 14)


def test_raw_prices_no_open_candle_and_fx_inversion(tmp_path):
    from bracket22.real_data import YahooProvider

    calls = []

    def download(ticker):
        calls.append(ticker)
        return pd.DataFrame(
            {
                "Open": [100.0, 110.0],
                "High": [102.0, 112.0],
                "Low": [99.0, 109.0],
                "Close": [101.0, 111.0],
                "Volume": [1000.0, 1000.0],
            },
            index=pd.to_datetime(["2026-09-11", "2026-09-14"]),
        )

    p = YahooProvider(tmp_path, now=lambda: datetime(2026, 9, 14, 15, tzinfo=UTC), download=download)
    assert len(p.history("SPY", date(2026, 9, 14))) == 1
    assert p.history("SPY", date(2026, 9, 14))[0].close == 101
    assert calls == ["SPY"]
    assert p.fx_to_eur("USD", date(2026, 9, 14)) == pytest.approx(1 / 101)
    assert p.metadata("SPY", date(2026, 9, 14))["fx_date"] == "2026-09-11"
    assert p.history("BTC", date(2026, 9, 14))[0].close == 101
    assert "BTC-USD" in calls


def test_invalid_provider_never_falls_back_to_synthetic(tmp_path):
    from bracket22.real_data import YahooProvider

    p = YahooProvider(tmp_path, download=lambda ticker: pd.DataFrame())
    with pytest.raises(ValueError, match="No data"):
        p.history("SPY", p.now().date())


def test_old_fx_is_rejected(tmp_path):
    from bracket22.real_data import YahooProvider

    def download(ticker):
        return pd.DataFrame(
            {"Open": [1.1], "High": [1.1], "Low": [1.1], "Close": [1.1], "Volume": [0.0]},
            index=pd.to_datetime(["2026-09-01"]),
        )

    p = YahooProvider(tmp_path, now=lambda: datetime(2026, 9, 14, 15, tzinfo=UTC), download=download)
    with pytest.raises(ValueError, match="Stale FX"):
        p.fx_to_eur("USD", date(2026, 9, 14))


def test_fx_uses_close_not_inconsistent_vendor_intraday_range(tmp_path):
    from bracket22.real_data import YahooProvider

    def download(ticker):
        return pd.DataFrame(
            {"Open": [1.1], "High": [1.1], "Low": [1.1], "Close": [1.2], "Volume": [0.0]},
            index=pd.to_datetime(["2026-09-11"]),
        )

    p = YahooProvider(tmp_path, now=lambda: datetime(2026, 9, 14, 15, tzinfo=UTC), download=download)
    assert p.fx_to_eur("USD", date(2026, 9, 14)) == pytest.approx(1 / 1.2)


def test_split_factor_after_reference_date(tmp_path):
    from bracket22.real_data import YahooProvider

    def download(ticker):
        return pd.DataFrame(
            {
                "Open": [100.0, 50.0],
                "High": [100.0, 50.0],
                "Low": [100.0, 50.0],
                "Close": [100.0, 50.0],
                "Volume": [1.0, 1.0],
                "Stock Splits": [0.0, 2.0],
            },
            index=pd.to_datetime(["2026-09-10", "2026-09-11"]),
        )

    p = YahooProvider(tmp_path, now=lambda: datetime(2026, 9, 14, 15, tzinfo=UTC), download=download)
    assert p.split_factor("SPY", date(2026, 9, 10), date(2026, 9, 14)) == 2
    assert p.split_factor("SPY", date(2026, 9, 11), date(2026, 9, 14)) == 1


def test_cannot_mix_sources_in_existing_journal(tmp_path):
    from bracket22.core import SampleProvider
    from bracket22.service import Service

    db = f"sqlite:///{tmp_path}/source.db"
    s = Service(db, today=lambda: date(2026, 9, 11))
    s.analyze("SPY")

    class Other(SampleProvider):
        name = "different-provider"

    with pytest.raises(ValueError, match="source"):
        Service(db, provider=Other())


def test_refresh_feed_failure_is_isolated(tmp_path):
    from bracket22.core import SampleProvider
    from bracket22.service import Service

    class Feed(SampleProvider):
        failed = False

        def history(self, symbol, as_of):
            if self.failed and symbol == "SPY":
                raise ValueError("feed unavailable")
            return super().history(symbol, as_of)

    p = Feed()
    s = Service(f"sqlite:///{tmp_path}/refresh.db", provider=p, today=lambda: date(2026, 9, 11))
    s.analyze("SPY")
    p.failed = True
    result = s.refresh()
    assert result["added"] == 0
    assert result["errors"][0]["symbol"] == "SPY"


def test_split_updates_paper_units_without_changing_journal(tmp_path):
    from bracket22.core import SampleProvider
    from bracket22.service import Service

    class SplitFeed(SampleProvider):
        split = False

        def history(self, symbol, as_of):
            bars = super().history(symbol, as_of)
            if self.split:
                return [
                    b.model_copy(update={k: getattr(b, k) / 2 for k in ("open", "high", "low", "close")})
                    for b in bars
                ]
            return bars

        def split_factor(self, symbol, reference_day, as_of):
            return 2.0 if self.split else 1.0

    p = SplitFeed()
    s = Service(f"sqlite:///{tmp_path}/split.db", provider=p, today=lambda: date(2026, 9, 11))
    r = s.analyze("SPY")
    fill = s.review(r["id"], "approve")
    p.split = True
    position = s.portfolio()["positions"][0]
    assert position["units"] == pytest.approx(fill["units"] * 2)
    assert position["value_eur"] == pytest.approx(5000)
    assert s.close("SPY")["proceeds_eur"] == pytest.approx(5000)
    assert s.store.verify()["valid"]
