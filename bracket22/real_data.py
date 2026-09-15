"""Real daily Yahoo market observations; no synthetic fallback, no execution API."""

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import RLock

import exchange_calendars as xcals
import yfinance as yf

from .core import UNIVERSE, Bar, MarketDataProvider


class YahooProvider(MarketDataProvider):
    name = "yahoo-daily-v1"

    def __init__(self, cache_dir="market-cache", now=None, download=None):
        self.now = now or (lambda: datetime.now(UTC))
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calendar = xcals.get_calendar("XNYS", start="2023-01-01", end="2030-12-31")
        self._download = download or self._fetch
        self._memory = {}
        self._lock = RLock()

    def _fetch(self, ticker):
        try:
            result = yf.Ticker(ticker).history(
                start="2023-01-01",
                interval="1d",
                auto_adjust=False,
                actions=True,
                raise_errors=True,
                timeout=20,
            )
        except Exception as exc:
            raise ValueError(f"Yahoo unavailable for {ticker}: {type(exc).__name__}") from exc
        return result

    def _observations(self, ticker):
        # Each fetched batch is retained as an immutable dated cache file.
        key = f"{ticker.replace('=', '_')}_{self.now().strftime('%Y%m%dT%H')}"
        with self._lock:
            if key in self._memory:
                return self._memory[key]
            path = self.cache_dir / f"{key}.json"
            if path.exists():
                result = json.loads(path.read_text())
            else:
                frame = self._download(ticker)
                if frame is None or frame.empty:
                    raise ValueError(f"No data for {ticker}")
                rows = []
                for timestamp, row in frame.iterrows():
                    if ticker == "EURUSD=X":
                        close = float(row["Close"])
                        if not math.isfinite(close) or close <= 0:
                            raise ValueError("Invalid FX quote")
                        rows.append({"day": timestamp.date().isoformat(), "close": close})
                        continue
                    bar = Bar(
                        day=timestamp.date(),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row["Volume"]),
                    )
                    rows.append(
                        {
                            **bar.model_dump(mode="json"),
                            "split": float(row.get("Stock Splits", 0)),
                            "dividend": float(row.get("Dividends", 0)),
                        }
                    )
                result = {"ticker": ticker, "fetched_at": self.now().isoformat(), "rows": rows}
                try:
                    with path.open("x") as out:
                        json.dump(result, out, allow_nan=False)
                except FileExistsError:
                    result = json.loads(path.read_text())
            self._memory = {key: result, **{k: v for k, v in self._memory.items() if k.endswith(key[-11:])}}
            return result

    def expected_date(self, symbol: str, as_of: date):
        if symbol not in UNIVERSE:
            raise KeyError(symbol)
        now = self.now()
        if as_of > now.date():
            raise ValueError("Future as_of is not permitted")
        if UNIVERSE[symbol].crypto:
            return min(as_of, now.date() - timedelta(days=1))
        session = self.calendar.date_to_session(as_of.isoformat(), direction="previous")
        if self.calendar.session_close(session).to_pydatetime() + timedelta(minutes=30) > now:
            session = self.calendar.previous_session(session)
        return session.date()

    def history(self, symbol, as_of):
        last = self.expected_date(symbol, as_of)
        ticker = {"BTC": "BTC-USD", "ETH": "ETH-USD"}.get(symbol, symbol)
        result = self._observations(ticker)
        return [Bar.model_validate(row) for row in result["rows"] if date.fromisoformat(row["day"]) <= last]

    def _fx(self, as_of):
        if as_of > self.now().date():
            raise ValueError("Future FX requested")
        last = min(as_of, self.now().date() - timedelta(days=1))
        rows = [r for r in self._observations("EURUSD=X")["rows"] if date.fromisoformat(r["day"]) <= last]
        if not rows:
            raise ValueError("No completed FX observation")
        row = rows[-1]
        if (last - date.fromisoformat(row["day"])).days > 4:
            raise ValueError("Stale FX observation")
        if not math.isfinite(row["close"]) or row["close"] <= 0:
            raise ValueError("Invalid FX quote")
        return {"date": row["day"], "rate": 1 / row["close"]}

    def fx_to_eur(self, currency, as_of):
        if currency == "EUR":
            return 1.0
        if currency != "USD":
            raise ValueError("Unsupported currency")
        return self._fx(as_of)["rate"]

    def metadata(self, symbol, as_of):
        ticker = {"BTC": "BTC-USD", "ETH": "ETH-USD"}.get(symbol, symbol)
        return {
            "source": "Yahoo Finance",
            "ticker": ticker,
            "fetched_at": self._observations(ticker)["fetched_at"],
            "expected_bar_date": self.expected_date(symbol, as_of).isoformat(),
            "fx_source": "Yahoo Finance EURUSD=X (inverted)",
            "fx_date": self._fx(as_of)["date"],
            "price_convention": "Yahoo Close, split-adjusted, not dividend-adjusted",
            "frequency": "completed daily bars; not real-time",
        }

    def split_factor(self, symbol, reference_day, as_of):
        ticker = {"BTC": "BTC-USD", "ETH": "ETH-USD"}.get(symbol, symbol)
        end = self.expected_date(symbol, as_of)
        result = 1.0
        for row in self._observations(ticker)["rows"]:
            if reference_day < date.fromisoformat(row["day"]) <= end and row.get("split", 0):
                result *= row["split"]
        return result
