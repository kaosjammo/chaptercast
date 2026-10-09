"""Application factory with private runtime initialisation and static UI."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chaptercast.config import Settings
from chaptercast.database import initialise

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ("uploads", "models", "audio"):
            (settings.data_dir / name).mkdir(mode=0o700, exist_ok=True)
        initialise(settings.data_dir / "metadata.sqlite3")
        yield

    app = FastAPI(title="ChapterCast", lifespan=lifespan)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def shell() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
