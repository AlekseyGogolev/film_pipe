from __future__ import annotations

try:
    from fastapi import FastAPI
except ImportError:  # pragma: no cover - exercised only without installed deps
    FastAPI = None  # type: ignore[assignment]


def create_app():
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI is not installed. Install project dependencies with "
            "`python -m pip install -e .` before starting the local API."
        )

    app = FastAPI(
        title="FilmPipe",
        version="0.1.0",
        description="Local FilmPipe API foundation. Job endpoints are Agent 3 scope.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app() if FastAPI is not None else None
