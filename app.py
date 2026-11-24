import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analytics.router import router as analytics_router
from auth.router import router as auth_router
from db.database import check_connection

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def cors_origins() -> list[str]:
    """Orígenes permitidos; ``CYBERPEAK_CORS_ORIGINS`` separados por coma."""
    raw = os.getenv("CYBERPEAK_CORS_ORIGINS")
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_connection()
    yield


app = FastAPI(
    title="Cyberpeak API",
    version="0.1.0",
    description="Analítica conversacional sobre Northwind.",
    lifespan=lifespan,
)

@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Comprobación de salud para la plataforma de despliegue."""
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(analytics_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
