"""FastAPI application entrypoint for Kenwer Channel 3."""

from fastapi import FastAPI

from kenwer_c3 import __version__
from kenwer_c3.api.health import router as health_router
from kenwer_c3.api.learning import router as learning_router
from kenwer_c3.api.memory import router as memory_router
from kenwer_c3.api.retrieval import router as retrieval_router
from kenwer_c3.api.review import router as review_router
from kenwer_c3.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Kenwer Channel 3 — Cognitive Identity Layer",
        description=(
            "Persistent, episodic, and consolidated cognitive memory with "
            "gated belief revision. Does not issue flight commands."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.include_router(health_router)
    app.include_router(memory_router)
    app.include_router(learning_router)
    app.include_router(review_router)
    app.include_router(retrieval_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
            "ready": "/ready",
        }

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("kenwer_c3.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    run()
