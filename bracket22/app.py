import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import configured_agents, configured_database, configured_provider
from .core import ASSETS
from .service import Conflict, Service
from .storage import StorageBusy


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)


def create_app(database_url=None, provider=None, today=None):
    @asynccontextmanager
    async def lifespan(app):
        if os.getenv("BRACKET22_REQUIRE_AUTH") == "true" and len(os.getenv("BRACKET22_API_TOKEN", "")) < 32:
            raise ValueError("BRACKET22_API_TOKEN must contain at least 32 characters for hosted access")
        app.state.service = Service(
            database_url or configured_database(), provider or configured_provider(), today,
            houston=configured_agents() if database_url is None else None
        )
        yield
        app.state.service.store.engine.dispose()

    app = FastAPI(title="Bracket22 Mini • Paper only", version="0.1.0", lifespan=lifespan)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def security(request: Request, call_next):
        token = os.getenv("BRACKET22_API_TOKEN", "")
        public = (
            request.url.path == "/"
            or request.url.path.startswith("/static/")
            or request.url.path == "/health"
        )
        if (
            token
            and not public
            and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}")
        ):
            return JSONResponse({"detail": "Jeton requis"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        if request.url.path in ("/docs", "/redoc"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
            )
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Actif ou rapport introuvable"}, status_code=404)

    @app.exception_handler(StorageBusy)
    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def service():
        return app.state.service

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "paper", "provider": service().provider.name, "agent_mode": getattr(service().houston, "mode", "deterministic-demo")}

    @app.get("/operations")
    def operations():
        return service().operations()

    @app.get("/assets")
    def assets():
        return ASSETS

    @app.get("/market/{symbol}")
    def market(symbol: str):
        return service().market(symbol.upper())

    @app.post("/analysis/run-daily")
    def daily():
        return service().daily()

    @app.post("/analysis/{symbol}")
    def analyze(symbol: str):
        return service().summary(service().analyze(symbol.upper()))

    @app.get("/reports/latest")
    def latest():
        return service().latest()

    @app.get("/reports/{symbol}")
    def report(symbol: str):
        service().asset(symbol.upper())
        matches = [r for r in service().latest() if r["symbol"] == symbol.upper()]
        if not matches:
            raise KeyError(symbol)
        return matches[0]

    @app.get("/opportunities")
    def opportunities():
        return [
            r
            for r in service().latest()
            if r["risk"]["status"] == "APPROVED" and r["human_review"] == "pending"
        ]

    @app.get("/risks")
    def risks():
        return {
            "rules": service().risk.rules,
            "reports": [
                {"symbol": r["symbol"], "risk": r["risk"], "red_team": r["agents"].get("Red Team")}
                for r in service().latest()
            ],
        }

    @app.post("/proposals/{report_id}/review")
    def review(report_id: str, body: Review):
        return service().review(report_id, body.action, body.reason)

    @app.get("/portfolio")
    def portfolio():
        return service().portfolio()

    @app.post("/portfolio/{symbol}/close")
    def close(symbol: str):
        return service().close(symbol.upper())

    @app.get("/performance")
    def performance():
        return service().performance()

    @app.post("/performance/refresh")
    def refresh():
        return service().refresh()

    @app.get("/agents/performance")
    def agents():
        return service().agent_performance()

    @app.get("/journal")
    def journal():
        result = service().store.verify()
        with service().store.engine.connect() as conn:
            result["events"] = service().store.all_events(conn)
        return result

    return app


app = create_app()
