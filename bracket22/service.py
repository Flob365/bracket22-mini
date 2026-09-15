import math
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from .agents import Houston
from .core import ACTIVE_SYMBOLS, ASSETS, UNIVERSE, RiskEngine, SampleProvider
from .storage import Store, canonical, measurements, reports


class Conflict(Exception):
    pass


class BrokerProvider(ABC):
    """Internal paper accounting contract; no credentials or network execution."""

    @abstractmethod
    def buy(self, *, symbol, notional_eur, price, fx, report_id, day): ...


class PaperBroker(BrokerProvider):
    def buy(self, *, symbol, notional_eur, price, fx, report_id, day):
        return {
            "symbol": symbol,
            "notional_eur": notional_eur,
            "units": notional_eur / (price * fx),
            "price": price,
            "fx_to_eur": fx,
            "report_id": report_id,
            "day": day.isoformat(),
            "mode": "paper",
        }


class Service:
    version = "v1"

    def __init__(self, url, provider=None, today=None, houston=None):
        self.store = Store(url)
        self.provider = provider or SampleProvider()
        self.today = today or (lambda: datetime.now(UTC).date())
        self.risk = RiskEngine()
        self.houston = houston or Houston()
        self.version = getattr(self.houston, "version", "v1")
        self.paper = PaperBroker()
        with self.store.transaction() as conn:
            sources = {r["provider"] for r in self.store.all_reports(conn)}
            bindings = [e for e in self.store.all_events(conn) if e["kind"] == "SOURCE_BOUND"]
            sources.update(e["payload"]["provider"] for e in bindings)
            if sources - {self.provider.name}:
                raise ValueError("Cannot mix market data sources; use a separate database")
            if not bindings:
                self.store.append(conn, "SOURCE_BOUND", {"provider": self.provider.name})

    def asset(self, symbol):
        if symbol not in ACTIVE_SYMBOLS:
            raise KeyError(symbol)
        return UNIVERSE[symbol]

    def market(self, symbol, day=None):
        asset, day = self.asset(symbol), day or self.today()
        bars = self.provider.history(symbol, day)
        if any(b.day > day for b in bars):
            raise ValueError("Provider returned future data")
        dates = [b.day for b in bars]
        if dates != sorted(set(dates)):
            raise ValueError("Unsorted or duplicate market dates")
        if bars:
            expected = []
            cursor = bars[0].day
            while cursor <= bars[-1].day:
                if self.provider.expected_date(symbol, cursor) == cursor:
                    expected.append(cursor)
                cursor += timedelta(days=1)
            if dates != expected:
                raise ValueError("Missing market sessions")
        fx = self.provider.fx_to_eur(asset.currency, day)
        if not math.isfinite(fx) or fx <= 0:
            raise ValueError("Invalid FX")
        return {
            "symbol": symbol,
            "provider": self.provider.name,
            "currency": asset.currency,
            "as_of": day.isoformat(),
            "fx_to_eur": fx,
            "provenance": self.provider.metadata(symbol, day),
            "stale": not bars or bars[-1].day < self.provider.expected_date(symbol, day),
            "bars": [b.model_dump(mode="json") for b in bars],
        }

    def _portfolio(self, conn):
        cash, positions, trades = 100000.0, {}, []
        for event in self.store.all_events(conn):
            p = event["payload"]
            if event["kind"] == "PAPER_BUY":
                cash -= p["notional_eur"]
                positions[p["symbol"]] = dict(p)
                trades.append(p)
            elif event["kind"] == "PAPER_CLOSE":
                cash += p["proceeds_eur"]
                positions.pop(p["symbol"], None)
                trades.append(p)
        missing, stale = False, False
        for symbol, pos in positions.items():
            report = self.store.report(conn, pos["report_id"])
            reference_day = date.fromisoformat(pos.get("reference_day", pos["day"]))
            pos.update(entry_price=pos["price"], current_price=None, current_fx_to_eur=None,
                       holding_days=(self.today() - date.fromisoformat(pos["day"])).days,
                       holding_sessions=None, horizon_sessions=report["horizon_days"],
                       stop_price=None, data_status="missing", exit_alerts=[])
            try:
                market = self.market(symbol)
                stale |= market["stale"]
                if not market["bars"]:
                    raise ValueError("Missing price")
                split = self.provider.split_factor(symbol, reference_day, self.today())
                stop_reference = date.fromisoformat(report.get("reference_day") or report["day"])
                stop_split = self.provider.split_factor(symbol, stop_reference, self.today())
                if any(not math.isfinite(v) or v <= 0 for v in (split, stop_split)):
                    raise ValueError("Invalid split factor")
                pos["units"] *= split
                pos["entry_price"] = pos["price"] / split
                pos["stop_price"] = report["stop_theoretical"] / stop_split if report["stop_theoretical"] else None
                pos["current_price"] = market["bars"][-1]["close"]
                pos["current_fx_to_eur"] = market["fx_to_eur"]
                pos["data_status"] = "stale" if market["stale"] else "fresh"
                pos["holding_sessions"] = sum(b["day"] > reference_day.isoformat() for b in market["bars"])
                if not market["stale"]:
                    if pos["stop_price"] is not None and pos["current_price"] <= pos["stop_price"]:
                        pos["exit_alerts"].append("stop_reached")
                    if pos["holding_sessions"] >= pos["horizon_sessions"]:
                        pos["exit_alerts"].append("horizon_reached")
                pos["value_eur"] = pos["units"] * market["bars"][-1]["close"] * market["fx_to_eur"]
                pos["mark_day"] = market["bars"][-1]["day"]
            except (ValueError, OSError):
                missing = True
                pos["value_eur"] = pos["notional_eur"]
                pos["mark_day"] = pos["day"]
            pos["pnl_eur"] = pos["value_eur"] - pos["notional_eur"] if pos["data_status"] != "missing" else None
            pos["return"] = pos["pnl_eur"] / pos["notional_eur"] if pos["pnl_eur"] is not None else None
        equity = cash + sum(p["value_eur"] for p in positions.values())
        for p in positions.values():
            p["weight"] = p["value_eur"] / equity
        return {
            "mode": "paper",
            "currency": "EUR",
            "provider": self.provider.name,
            "initial_capital_eur": 100000,
            "realized_pnl_eur": sum(t.get("realized_pnl_eur", 0) for t in trades),
            "unrealized_pnl_eur": None if missing else sum(p["pnl_eur"] for p in positions.values()),
            "valuation_estimated": missing or stale,
            "cash_eur": cash,
            "equity_eur": equity,
            "pnl_eur": equity - 100000,
            "return": equity / 100000 - 1,
            "positions": list(positions.values()),
            "trades": trades,
            "missing_data": missing,
            "stale_data": stale,
        }

    def portfolio(self):
        with self.store.engine.connect() as conn:
            return self._portfolio(conn)

    def _risk(self, conn, report, stale=False, missing=False):
        p = self._portfolio(conn)
        a = self.asset(report["symbol"])
        sector = sum(v["weight"] for v in p["positions"] if UNIVERSE[v["symbol"]].sector == a.sector)
        crypto = sum(v["weight"] for v in p["positions"] if UNIVERSE[v["symbol"]].crypto)
        new_today = sum(
            t.get("mode") == "paper" and "notional_eur" in t and t["day"] == self.today().isoformat()
            for t in p["trades"]
        )
        h = report["agents"]["Houston"]
        result = self.risk.evaluate(
            weight=0.05,
            sector_weight=sector,
            crypto_weight=crypto,
            crypto=a.crypto,
            new_today=new_today,
            score=h["score"],
            confidence=h["confidence"],
            missing=missing or p["missing_data"],
            stale=stale or p["stale_data"],
        )
        extra = []
        if any(v["symbol"] == a.symbol for v in p["positions"]):
            extra.append("position_already_open")
        if p["cash_eur"] < p["equity_eur"] * 0.05:
            extra.append("insufficient_cash")
        if h["recommendation"] == "watch":
            extra.append("red_team_veto")
        if extra:
            result["status"] = "REJECTED"
            result["reasons"] += extra
        return result

    def analyze(self, symbol):
        self.asset(symbol)
        day = self.today()
        key = f"{symbol}:{day}:{self.version}"
        # Calculations are inside the transaction so concurrent calls produce one decision.
        with self.store.transaction() as conn:
            attempts = [r for r in self.store.all_reports(conn)
                        if r["symbol"] == symbol and r["day"] == day.isoformat()
                        and r["version"] == self.version]
            existing = max(attempts, key=lambda r: r["created_at"], default=None)
            if existing and not self.invalid_data(existing):
                return existing
            if existing:
                key += f":retry:{len(attempts)}"
            market, benchmark, agents, error = None, None, None, None
            try:
                market, benchmark = self.market(symbol, day), self.market("SPY", day)
                from .core import Bar

                bars = [Bar.model_validate(b) for b in market["bars"]]
                bench = [Bar.model_validate(b) for b in benchmark["bars"]]
                if len(bars) < 260 or len(bench) < 260:
                    raise ValueError(f"Historique insuffisant : {len(bars)} séances pour l’actif ; 260 requises.")
                agents = self.houston.run(bars, bench, UNIVERSE[symbol].crypto)
            except (ValueError, OSError) as exc:
                error = str(exc)
                agents = {
                    "Houston": {
                        "agent": "Houston",
                        "score": 0.0,
                        "confidence": 0.0,
                        "recommendation": "watch",
                        "signal": False,
                        "thesis": "Données insuffisantes.",
                    }
                }
            report = {
                "id": str(uuid.uuid4()),
                "supersedes": existing["id"] if existing else None,
                "symbol": symbol,
                "day": day.isoformat(),
                "created_at": datetime.now(UTC).isoformat(),
                "version": self.version,
                "provider": self.provider.name,
                "mode": getattr(self.houston, "mode", "deterministic-demo"),
                "reference_price": market["bars"][-1]["close"] if market and market["bars"] else None,
                "fx_to_eur": market["fx_to_eur"] if market else None,
                "reference_day": market["bars"][-1]["day"] if market and market["bars"] else None,
                "provenance": market["provenance"] if market else None,
                "agents": agents,
                "data_error": error,
                "market_snapshot": market,
                "benchmark_snapshot": benchmark,
                "proposed_weight": 0.05,
                "stop_theoretical": agents.get("Steffi", {}).get("metrics", {}).get("stop"),
                "horizon_days": 20,
            }
            report["risk"] = self._risk(
                conn,
                report,
                missing=error is not None,
                stale=bool(market and market["stale"]) or bool(benchmark and benchmark["stale"]),
            )
            conn.execute(reports.insert().values(id=report["id"], key=key, payload=canonical(report)))
            for name, result in agents.items():
                self.store.append(
                    conn, "AGENT_RUN", {"report_id": report["id"], "agent": name, "output": result}
                )
            self.store.append(conn, "ANALYSIS", report)
            return report

    def daily(self):
        output, errors = [], []
        for symbol in (a.symbol for a in ASSETS):
            try:
                report = self.analyze(symbol)
                output.append(report)
                if self.invalid_data(report):
                    errors.append({"symbol": symbol, "error": report["data_error"] or "Stale market data"})
            except (ValueError, OSError) as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
        measured = self.refresh()
        errors.extend(measured["errors"])
        result = {
            "day": self.today().isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "measurements_added": measured["added"],
            "reports": [self.summary(r) for r in output],
            "errors": errors,
        }

        with self.store.transaction() as conn:
            self.store.append(conn, "DAILY_RUN", {k: v for k, v in result.items() if k != "reports"})
        return result

    @staticmethod
    def invalid_data(report):
        return bool(report["data_error"] or any(
            report.get(name) and report[name]["stale"]
            for name in ("market_snapshot", "benchmark_snapshot")
        ))

    def operations(self):
        with self.store.engine.connect() as conn:
            runs = [e["payload"] for e in self.store.all_events(conn) if e["kind"] == "DAILY_RUN"]
        successful = [r for r in runs if not r["errors"]]
        return {"last_run": runs[-1] if runs else None,
                "last_success": successful[-1] if successful else None,
                "scheduled_hour_utc": 7, "retry_minutes": 60,
                "scheduler_requires_running_process": True}

    @staticmethod
    def summary(report):
        return {k: v for k, v in report.items() if k not in ("market_snapshot", "benchmark_snapshot")}

    def latest(self):
        with self.store.engine.connect() as conn:
            rows = self.store.all_reports(conn)
            events = self.store.all_events(conn)
        latest = {}
        for report in sorted(rows, key=lambda r: (r["day"], r["created_at"])):
            if report["symbol"] in ACTIVE_SYMBOLS:
                latest[report["symbol"]] = self.summary(report)
        reviews = {
            e["payload"]["report_id"]: e["payload"]["action"] for e in events if e["kind"] == "HUMAN_REVIEW"
        }
        for r in latest.values():
            r["human_review"] = reviews.get(r["id"], "pending")
        return sorted(latest.values(), key=lambda r: r["agents"]["Houston"]["score"], reverse=True)

    def review(self, report_id, action, reason=""):
        failure = None
        with self.store.transaction() as conn:
            report = self.store.report(conn, report_id)
            if not report:
                raise KeyError(report_id)
            if action == "approve" and report["symbol"] not in ACTIVE_SYMBOLS:
                raise Conflict("Actif retiré de l’univers suivi")
            if any(
                e["kind"] == "HUMAN_REVIEW" and e["payload"]["report_id"] == report_id
                for e in self.store.all_events(conn)
            ):
                raise Conflict("Proposition déjà examinée")
            if action == "reject":
                result = {"report_id": report_id, "action": action, "reason": reason}
                self.store.append(conn, "HUMAN_REVIEW", result)
                return result
            try:
                market = self.market(report["symbol"])
            except (ValueError, OSError):
                market = {"bars": [], "stale": True}
            risk = self._risk(
                conn,
                report,
                stale=market["stale"] or report["day"] != self.today().isoformat(),
                missing=not market["bars"] or report["data_error"] is not None,
            )
            if report["risk"]["status"] != "APPROVED":
                risk["status"] = "REJECTED"
                risk["reasons"].append("original_proposal_not_approved")
            if risk["status"] != "APPROVED":
                self.store.append(conn, "RISK_RECHECK_REJECTED", {"report_id": report_id, "risk": risk})
                failure = ", ".join(risk["reasons"])
            else:
                portfolio = self._portfolio(conn)
                fill = self.paper.buy(
                    symbol=report["symbol"],
                    notional_eur=portfolio["equity_eur"] * 0.05,
                    price=market["bars"][-1]["close"],
                    fx=market["fx_to_eur"],
                    report_id=report_id,
                    day=self.today(),
                )
                fill["reference_day"] = market["bars"][-1]["day"]
                self.store.append(conn, "RISK_RECHECK_APPROVED", {"report_id": report_id, "risk": risk})
                self.store.append(
                    conn, "HUMAN_REVIEW", {"report_id": report_id, "action": action, "reason": reason}
                )
                self.store.append(conn, "PAPER_BUY", fill)
                result = fill
        if failure:
            raise Conflict(failure)
        return result

    def close(self, symbol):
        self.asset(symbol)
        with self.store.transaction() as conn:
            position = next((p for p in self._portfolio(conn)["positions"] if p["symbol"] == symbol), None)
            if not position:
                raise Conflict("Aucune position ouverte")
            market = self.market(symbol)
            if not market["bars"] or market["stale"]:
                raise Conflict("Prix absent ou périmé")
            proceeds = position["units"] * market["bars"][-1]["close"] * market["fx_to_eur"]
            fill = {
                "symbol": symbol,
                "day": self.today().isoformat(),
                "units": position["units"],
                "price": market["bars"][-1]["close"],
                "fx_to_eur": market["fx_to_eur"],
                "proceeds_eur": proceeds,
                "realized_pnl_eur": proceeds - position["notional_eur"],
                "report_id": position["report_id"],
                "mode": "paper",
            }
            self.store.append(conn, "PAPER_CLOSE", fill)
            return fill

    def refresh(self):
        added = 0
        errors = []
        with self.store.transaction() as conn:
            for r in self.store.all_reports(conn):
                if r["symbol"] not in ACTIVE_SYMBOLS or r["reference_price"] is None or self.invalid_data(r):
                    continue
                day = date.fromisoformat(r.get("reference_day") or r["day"])
                try:
                    from .core import Bar

                    bars = [Bar.model_validate(b) for b in self.market(r["symbol"])["bars"]]
                    benchmark = [Bar.model_validate(b) for b in self.market("SPY")["bars"]]
                    bench_ref_day = date.fromisoformat(r["benchmark_snapshot"]["bars"][-1]["day"])
                    asset_split = self.provider.split_factor(r["symbol"], day, self.today())
                    bench_split = self.provider.split_factor("SPY", bench_ref_day, self.today())
                    if any(not math.isfinite(v) or v <= 0 for v in (asset_split, bench_split)):
                        raise ValueError("Invalid split factor")
                except (ValueError, OSError) as exc:
                    errors.append({"symbol": r["symbol"], "error": str(exc)})
                    continue
                future = [b for b in bars if day < b.day <= self.today()]
                ref_bench = r["benchmark_snapshot"]["bars"][-1]["close"]
                for horizon in (1, 5, 20, 60):
                    key = f"{r['id']}:{horizon}"
                    if (
                        len(future) < horizon
                        or conn.execute(select(measurements.c.key).where(measurements.c.key == key)).first()
                    ):
                        continue
                    end = future[horizon - 1]
                    bench_at_end = [b for b in benchmark if b.day <= end.day]
                    if not bench_at_end:
                        continue
                    benchmark_return = (
                        bench_at_end[-1].close
                        * bench_split
                        / ref_bench
                        - 1
                    )
                    value = (
                        end.close
                        * asset_split
                        / r["reference_price"]
                        - 1
                    )
                    measurement = {
                        "report_id": r["id"],
                        "symbol": r["symbol"],
                        "horizon": horizon,
                        "day": end.day.isoformat(),
                        "reference_price": r["reference_price"],
                        "end_price": end.close,
                        "return": value,
                        "benchmark_return": benchmark_return,
                        "alpha": value - benchmark_return,
                        "provider": self.provider.name,
                    }
                    conn.execute(measurements.insert().values(key=key, payload=canonical(measurement)))
                    self.store.append(conn, "FORWARD_MEASURED", measurement)
                    added += 1
        return {"added": added, "errors": errors}

    def performance(self):
        import json

        with self.store.engine.connect() as conn:
            observations = [
                json.loads(p)
                for p in conn.execute(select(measurements.c.payload).order_by(measurements.c.key)).scalars()
            ]
        return {
            "portfolio": self.portfolio(),
            "observations": observations,
            "convention": "sessions after reference close; split-adjusted price returns, dividends excluded; SPY benchmark",
            "provider": self.provider.name,
        }

    def agent_performance(self):
        with self.store.engine.connect() as conn:
            report_map = {r["id"]: r for r in self.store.all_reports(conn)
                          if r["symbol"] in ACTIVE_SYMBOLS and r["version"] == self.version and not self.invalid_data(r)}
        observations = self.performance()["observations"]
        output = {}
        for name in ("Steffi", "Desmond", "Red Team", "Houston"):
            signals = [r for r in report_map.values() if r["agents"].get(name, {}).get("signal")]
            signal_ids = {r["id"] for r in signals}
            horizons = {}
            for horizon in (1, 5, 20, 60):
                rows = [o for o in observations if o["horizon"] == horizon and o["report_id"] in signal_ids]
                count = len(rows)
                buckets, regimes = {}, {}
                for o in rows:
                    r = report_map[o["report_id"]]
                    confidence = r["agents"][name]["confidence"]
                    bucket = (
                        str(min(9, int(confidence * 10)) * 10)
                        + "–"
                        + str(min(10, int(confidence * 10) + 1) * 10)
                        + "%"
                    )
                    buckets.setdefault(bucket, []).append(o["return"])
                    regime = r["agents"].get("Steffi", {}).get("metrics", {}).get("regime", "unknown")
                    regimes.setdefault(regime, []).append(o["return"])

                def aggregate(values):
                    return {
                        "count": len(values),
                        "win_rate": sum(v > 0 for v in values) / len(values),
                        "mean_return": sum(values) / len(values),
                    }

                horizons[str(horizon)] = {
                    "count": count,
                    "win_rate": sum(o["return"] > 0 for o in rows) / count if count else None,
                    "mean_return": sum(o["return"] for o in rows) / count if count else None,
                    "mean_alpha": sum(o["alpha"] for o in rows) / count if count else None,
                    "calibration": {k: aggregate(v) for k, v in buckets.items()},
                    "by_regime": {k: aggregate(v) for k, v in regimes.items()},
                }
            output[name] = {"signals": len(signals), "horizons": horizons, "mode": getattr(self.houston, "mode", "deterministic-demo")}
        return output
