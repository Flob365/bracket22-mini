"""Offline agent adapter: independent specialist roles, only deterministic tool outputs.

These roles are deliberately not advertised as LLM agents. Replace this adapter with
model-based interpretation later; keep calculations and risk enforcement in Python.
"""

from .core import DataEngine


class Steffi:
    name = "Steffi"

    def run(self, tools, bars, benchmark, annualization):
        data = tools.calculate(bars, benchmark, annualization)
        return {
            "agent": self.name,
            "mode": "deterministic-demo",
            "tool": tools.version,
            "score": data["technical_score"],
            "confidence": data["technical_confidence"],
            "trend": data["trend"],
            "signal": data["trend"] == "bullish",
            "support": data["support"],
            "resistance": data["resistance"],
            "thesis": "Tendance déterminée par le prix et les moyennes 50/200 séances.",
            "invalidation": "Clôture sous le support ou retournement de tendance.",
            "metrics": data,
        }


class Desmond:
    name = "Desmond"

    def run(self, tools, bars, benchmark, annualization):
        data = tools.calculate(bars, benchmark, annualization)
        return {
            "agent": self.name,
            "mode": "deterministic-demo",
            "tool": tools.version,
            "score": data["quant_score"],
            "confidence": data["quant_confidence"],
            "signal": data["quant_score"] >= 7,
            "thesis": "Échantillons espacés de 20 séances ; coûts de backtest de 20 pdb.",
            "metrics": data,
        }


class RedTeam:
    name = "Red Team"

    def run(self, technical, quant):
        metrics = quant["metrics"]
        arguments = [
            "Résultats historiques exploratoires : aucune preuve de performance future.",
            "Actualités, résultats et valorisations non disponibles.",
            "Confiances heuristiques non calibrées ; biais de régime possible.",
        ]
        if metrics["observations"] < 30:
            arguments.append("Faible échantillon statistique ; bootstrap exploratoire.")
        if abs(metrics["correlation_spy"]) > 0.8:
            arguments.append("Forte corrélation au benchmark SPY.")
        if technical["signal"] != quant["signal"]:
            arguments.append("Contradiction entre les spécialistes.")
        severe = metrics["volatility"] > 0.6 or metrics["observations"] < 10
        return {
            "agent": self.name,
            "mode": "deterministic-demo",
            "severity": "high" if severe else "medium",
            "risk_score": 8 if severe else 5,
            "arguments": arguments,
            "recommendation": "reject" if severe else "continue",
            "signal": not severe,
            "confidence": 0.6,
        }


class Houston:
    def __init__(self):
        self.tools = DataEngine()

    def run(self, bars, benchmark, crypto=False):
        annualization = 365 if crypto else 252
        steffi = Steffi().run(self.tools, bars, benchmark, annualization)
        desmond = Desmond().run(self.tools, bars, benchmark, annualization)
        red = RedTeam().run(steffi, desmond)
        summary = self.tools.synthesize(steffi, desmond, red)
        summary.update(
            agent="Houston",
            mode="deterministic-demo",
            signal=summary["recommendation"] == "candidate" and summary["score"] >= 7,
            thesis="Synthèse des tendances et de la distribution historique calculées par Python.",
            scenarios=desmond["metrics"]["scenarios"],
            major_risks=red["arguments"],
        )
        return {"Steffi": steffi, "Desmond": desmond, "Red Team": red, "Houston": summary}
