"""Explicit provider selection; separate default ledgers for each source."""

import os

from .core import SampleProvider


def configured_provider():
    name = os.getenv("MARKET_DATA_PROVIDER", "synthetic")
    if name == "synthetic":
        return SampleProvider()
    if name == "yahoo":
        from .real_data import YahooProvider

        return YahooProvider(os.getenv("MARKET_CACHE_DIR", "market-cache"))
    raise ValueError(f"Unknown market data provider: {name}")


def configured_database():
    default = "bracket22-real.db" if os.getenv("MARKET_DATA_PROVIDER") == "yahoo" else "bracket22.db"
    url = os.getenv("DATABASE_URL", f"sqlite:///./{default}")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def configured_agents():
    from pathlib import Path

    from dotenv import dotenv_values

    from .agents import Houston

    values = dotenv_values(Path(__file__).resolve().parent.parent / '.env.local')
    mode = os.getenv('AGENT_MODE', values.get('AGENT_MODE', 'deterministic'))
    if mode == 'deterministic':
        return Houston()
    if mode != 'llm':
        raise ValueError('Unknown AGENT_MODE')
    from .llm import LLMHouston
    key = os.getenv('OPENAI_API_KEY') or values.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('OPENAI_API_KEY is required in LLM mode')
    return LLMHouston(api_key=key)
