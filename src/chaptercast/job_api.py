"""Bounded submission and polling; requests never own generation execution."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
import json

from chaptercast.import_api import body
from chaptercast.jobs import GenerationSettings, JobFailure

router = APIRouter(prefix="/api/jobs")


@router.post("/preview", status_code=202)
async def preview(request: Request):
    raw = await body(request, 8192)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) - {"voice", "speed", "formats"}:
            raise ValueError("fields")
        settings = GenerationSettings(**data)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise JobFailure("invalid_input", "Provide supported preview settings.") from exc
    if request.app.state.worker.backend is None:
        raise JobFailure("backend_unavailable", "Install the tts extra and run chaptercast-models locally first.", 503)
    return await run_in_threadpool(request.app.state.jobs.preview, settings)


@router.post("", status_code=202)
async def enqueue(request: Request):
    raw = await body(request, 8192)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) - {"chapter_id", "voice", "speed", "formats"} or "chapter_id" not in data:
            raise ValueError("fields")
        settings = GenerationSettings(**{k: v for k, v in data.items() if k != "chapter_id"})
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise JobFailure("invalid_input", "Provide a chapter_id and supported generation settings.") from exc
    return await run_in_threadpool(request.app.state.jobs.enqueue, data["chapter_id"], settings)


@router.get("/worker")
def worker_status(request: Request):
    worker = request.app.state.worker
    return {"backend_ready": worker.backend is not None, "worker_owned": worker.owns_lock,
            "error": worker.last_error}


@router.get("/{identifier}")
def job(identifier: int, request: Request):
    return request.app.state.jobs.get(identifier)


@router.post("/{identifier}/cancel")
def cancel(identifier: int, request: Request):
    return request.app.state.jobs.cancel(identifier)


@router.get("/{identifier}/outputs/{format}")
def output(identifier: int, format: str, request: Request):
    path = request.app.state.jobs.output(identifier, format)
    return FileResponse(path, media_type={"opus": "audio/ogg", "m4a": "audio/mp4"}[format], filename=f"chapter.{format}")
