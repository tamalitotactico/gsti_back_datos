"""Punto de entrada de la API GSTI."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import auth, devices, participants, staff, ws
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=s.app_name,
        version="0.1.0",
        description=(
            "Backend del sistema de monitoreo comportamental (EPIS-UNSA). Recibe bioseñales, indicadores del "
            "smartphone y respuestas EMA/cuestionarios en JSON; guarda datos seudonimizados y genera alertas "
            "para revisión por un profesional autorizado. No emite diagnósticos."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    for r in (auth.router, participants.router, devices.router, staff.router):
        app.include_router(r, prefix=API_PREFIX)
    app.include_router(ws.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "environment": s.environment}

    return app


app = create_app()
