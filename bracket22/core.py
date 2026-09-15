"""Deterministic data and risk tools. No broker access or model calls."""

import hashlib
import math
from abc import ABC, abstractmethod
from datetime import date, timedelta
from functools import lru_cache
from typing import ClassVar

import numpy as np
import polars as pl
import statsmodels.api as sm
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.stats import percentileofscore
from sklearn.linear_model import LinearRegression


class Asset(BaseModel):
    symbol: str
    sector: str
    crypto: bool = False
    currency: str = "USD"


ASSETS = [
    Asset(symbol=s, sector=sector)
    for s, sector in [
        ("QQQ", "Growth ETF"),
        ("SPY", "Broad ETF"),
        ("AAPL", "Technology"),
        ("MSFT", "Technology"),
        ("NVDA", "Technology"),
        ("AMZN", "Consumer discretionary"),
        ("META", "Communication"),
        ("GOOGL", "Communication"),
        ("AVGO", "Technology"),
        ("TSLA", "Consumer discretionary"),
        ("AMD", "Technology"),
        ("NFLX", "Communication"),
        ("COST", "Consumer staples"),
        ("PLTR", "Technology"),
        ("LRCX", "Technology"),
        ("SPCX", "Industrials"),
        ("WMT", "Consumer staples"),
        ("GLD", "Gold"),
        ("TLT", "Bonds"),
        ("IWM", "Small-cap ETF"),
    ]
]
ACTIVE_SYMBOLS = frozenset(a.symbol for a in ASSETS)
# Keep retired instruments resolvable for historical paper accounting and provider tests.
UNIVERSE = {a.symbol: a for a in ASSETS}
UNIVERSE.update({a.symbol: a for a in (
    Asset(symbol="BTC", sector="Crypto", crypto=True),
    Asset(symbol="ETH", sector="Crypto", crypto=True),
    Asset(symbol="CRWD", sector="Technology"),
)})


class Bar(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    day: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_ohlc(self):
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLC range")
        return self


class MarketDataProvider(ABC):
    name: str

    def metadata(self, symbol: str, as_of: date) -> dict:
        return {"source": self.name}

    def split_factor(self, symbol: str, reference_day: date, as_of: date) -> float:
        return 1.0

    @abstractmethod
    def history(self, symbol: str, as_of: date) -> list[Bar]: ...

    @abstractmethod
    def fx_to_eur(self, currency: str, as_of: date) -> float: ...

    @abstractmethod
    def expected_date(self, symbol: str, as_of: date) -> date: ...


class SampleProvider(MarketDataProvider):
    name = "synthetic-v1"

    def expected_date(self, symbol, as_of):
        while not UNIVERSE[symbol].crypto and as_of.weekday() >= 5:
            as_of -= timedelta(days=1)
        return as_of

    def history(self, symbol, as_of):
        if symbol not in UNIVERSE:
            raise KeyError(symbol)
        return list(self._history(symbol, as_of))

    @staticmethod
    @lru_cache(maxsize=160)
    def _history(symbol, as_of):
        seed = int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:4], "big")
        rng = np.random.default_rng(seed)
        day, price, bars = date(2023, 1, 1), 50 + seed % 250, []
        i = 0
        while day <= as_of:
            if UNIVERSE[symbol].crypto or day.weekday() < 5:
                # Same sequence from a fixed epoch; extending history never revises its past.
                drift = 0.0011 if symbol not in ("TSLA", "TLT", "AMD") else -0.0003
                change = drift + 0.002 * math.sin(i / 17) + rng.normal(0, 0.004)
                close = price * math.exp(change)
                bars.append(
                    Bar(
                        day=day,
                        open=price,
                        high=max(price, close) * 1.003,
                        low=min(price, close) * 0.997,
                        close=close,
                        volume=float(1000000 + rng.integers(0, 500000)),
                    )
                )
                price, i = close, i + 1
            day += timedelta(days=1)
        return tuple(bars)

    def fx_to_eur(self, currency, as_of):
        if currency not in ("EUR", "USD"):
            raise ValueError("Unsupported sample currency")
        return 1.0 if currency == "EUR" else 0.92


class DataEngine:
    version = "tools-v1"

    def calculate(self, bars: list[Bar], benchmark: list[Bar], annualization=252) -> dict:
        if len(bars) < 260:
            raise ValueError("At least 260 bars required")
        days = [b.day for b in bars]
        if days != sorted(set(days)):
            raise ValueError("Bars must have unique ascending dates")
        frame = pl.DataFrame([b.model_dump() for b in bars])
        c, h, low, volume = (frame[col].to_numpy() for col in ("close", "high", "low", "volume"))
        ret = c[1:] / c[:-1] - 1
        sma50 = frame["close"].rolling_mean(50).to_numpy()
        sma200 = frame["close"].rolling_mean(200).to_numpy()
        delta = np.diff(c)
        gains, losses = np.maximum(delta, 0), np.maximum(-delta, 0)
        gain, loss = gains[:14].mean(), losses[:14].mean()
        for g, loss_i in zip(gains[14:], losses[14:]):
            gain, loss = (gain * 13 + g) / 14, (loss * 13 + loss_i) / 14
        rsi = 50 if gain == loss == 0 else (100 if loss == 0 else 100 - 100 / (1 + gain / loss))
        tr = np.maximum(h[1:] - low[1:], np.maximum(abs(h[1:] - c[:-1]), abs(low[1:] - c[:-1])))
        atr = tr[:14].mean()
        for value in tr[14:]:
            atr = (atr * 13 + value) / 14
        sigma = ret.std(ddof=1)
        downside = np.sqrt(np.mean(np.minimum(ret, 0) ** 2))
        bench_map = {b.day: b.close for b in benchmark}
        aligned = [(b.close, bench_map[b.day]) for b in bars if b.day in bench_map]
        if len(aligned) < 260:
            raise ValueError("Insufficient aligned benchmark history")
        pair = np.array(aligned)
        pair_returns = pair[1:] / pair[:-1] - 1
        x, y = pair_returns[:, 1], pair_returns[:, 0]
        correlation = float(np.corrcoef(x, y)[0, 1]) if x.std() > 1e-12 and y.std() > 1e-12 else 0.0
        beta = float(sm.OLS(y, sm.add_constant(x)).fit().params[-1]) if x.std() > 1e-12 else 0.0
        slope = float(LinearRegression().fit(np.arange(60).reshape(-1, 1), np.log(c[-60:])).coef_[0])
        forward = [c[i + 20] / c[i] - 1 - 0.002 for i in range(200, len(c) - 20, 20) if sma50[i] > sma200[i]]
        samples = np.array(forward)
        if len(samples):
            boot = np.random.default_rng(22).choice(samples, (1000, len(samples)), replace=True).mean(axis=1)
            quantiles = np.quantile(samples, [0.1, 0.5, 0.9]).tolist()
            ci = np.quantile(boot, [0.025, 0.975]).tolist()
        else:
            quantiles, ci = [0.0, 0.0, 0.0], [0.0, 0.0]
        trend = (
            "bullish" if c[-1] > sma50[-1] > sma200[-1] else "bearish" if c[-1] < sma200[-1] else "neutral"
        )
        technical_score = 8.0 if trend == "bullish" else 4.0 if trend == "neutral" else 2.0
        quant_score = 8.0 if len(samples) >= 10 and samples.mean() > 0 else 4.0
        return {
            "rsi14": float(rsi),
            "sma50": float(sma50[-1]),
            "sma200": float(sma200[-1]),
            "ema20": float(frame["close"].ewm_mean(span=20, adjust=False)[-1]),
            "atr14": float(atr),
            "momentum20": float(c[-1] / c[-21] - 1),
            "relative_strength20": float(pair[-1, 0] / pair[-21, 0] - pair[-1, 1] / pair[-21, 1]),
            "breakout": bool(c[-1] > h[-21:-1].max()),
            "support": float(low[-20:].min()),
            "resistance": float(h[-20:].max()),
            "stop": float(c[-1] - 2 * atr),
            "volume_anomaly": float(volume[-1] / max(volume[-21:-1].mean(), 1)),
            "volatility": float(sigma * np.sqrt(annualization)),
            "sharpe": float(ret.mean() / sigma * np.sqrt(annualization)) if sigma > 1e-12 else 0.0,
            "sortino": float(ret.mean() / downside * np.sqrt(annualization)) if downside > 1e-12 else 0.0,
            "max_drawdown": float((c / np.maximum.accumulate(c) - 1).min()),
            "correlation_spy": correlation,
            "beta_spy": beta,
            "log_trend_slope60": slope,
            "momentum_percentile": float(percentileofscore(c[20:] / c[:-20] - 1, c[-1] / c[-21] - 1)),
            "observations": len(samples),
            "forward_return_20d": float(samples.mean()) if len(samples) else 0.0,
            "positive_probability": float((samples > 0).mean()) if len(samples) else 0.0,
            "bootstrap_mean_ci95": ci,
            "scenarios": dict(zip(("bear", "base", "bull"), quantiles)),
            "trend": trend,
            "regime": "risk-on" if pair[-1, 1] > pair[-200:, 1].mean() else "risk-off",
            "technical_score": technical_score,
            "quant_score": quant_score,
            "technical_confidence": 0.70 if trend == "bullish" else 0.50,
            "quant_confidence": 0.65 if len(samples) >= 10 else 0.40,
        }

    def synthesize(self, technical, quant, red):
        return {
            "score": (technical["score"] + quant["score"]) / 2,
            "confidence": min(technical["confidence"], quant["confidence"]),
            "direction": "long",
            "horizon_days": 20,
            "recommendation": "watch" if red["recommendation"] == "reject" else "candidate",
        }


class RiskEngine:
    rules: ClassVar[dict] = {
        "max_position_weight": 0.05,
        "max_sector_weight": 0.25,
        "max_crypto_weight": 0.10,
        "max_new_positions_per_day": 3,
        "minimum_houston_score": 7,
        "minimum_confidence": 0.60,
        "reject_missing_data": True,
        "reject_stale_data": True,
    }

    def evaluate(
        self, *, weight, sector_weight, crypto_weight, crypto, new_today, score, confidence, missing, stale
    ):
        reasons = []
        numbers = [weight, sector_weight, crypto_weight, new_today, score, confidence]
        if (
            not all(math.isfinite(n) and n >= 0 for n in numbers)
            or weight <= 0
            or confidence > 1
            or score > 10
        ):
            reasons.append("invalid_numbers")
        if weight > 0.05 + 1e-10:
            reasons.append("position_limit")
        if sector_weight + weight > 0.25 + 1e-10:
            reasons.append("sector_limit")
        if crypto and crypto_weight + weight > 0.10 + 1e-10:
            reasons.append("crypto_limit")
        if new_today >= 3:
            reasons.append("daily_limit")
        if score < 7:
            reasons.append("score_below_minimum")
        if confidence < 0.6:
            reasons.append("confidence_below_minimum")
        if missing:
            reasons.append("missing_data")
        if stale:
            reasons.append("stale_data")
        hard = set(reasons) - {"score_below_minimum", "confidence_below_minimum"}
        return {
            "status": "REJECTED" if hard else "WATCH" if reasons else "APPROVED",
            "reasons": reasons,
            "rules": self.rules,
        }
