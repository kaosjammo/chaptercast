"""Private import endpoints. JSON errors never echo submitted story content."""

import json
from pathlib import Path
import re
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from chaptercast import ffn, notion
from chaptercast.database import connect
from chaptercast.imports import (
    Chapter, ImportFailure, MAX_TEXT_BYTES, MAX_UPLOAD_BYTES,
    decode_text, epub_chapter, read_epub, text_chapter,
)
from chaptercast.jobs import GenerationSettings, JobFailure
from chaptercast.pdf_import import pdf_chapter

router = APIRouter(prefix="/api/import")


async def body(request: Request, limit: int) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise ImportFailure("too_large", "Input exceeds the import size limit.", 413)
        data.extend(chunk)
    return bytes(data)


async def payload(request: Request, limit: int = MAX_TEXT_BYTES * 2) -> dict:
    raw = await body(request, limit)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ImportFailure("invalid_input", "Request must contain valid JSON.") from exc
    if not isinstance(value, dict):
        raise ImportFailure("invalid_input", "Request must be a JSON object.")
    return value


def field(data: dict, name: str, default=None) -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise ImportFailure("invalid_input", f"{name} must be text.")
    return value


def options(data: dict) -> tuple[str | None, bool]:
    title = data.get("title")
    if title is not None and (not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or any(ord(c) < 32 for c in title)):
        raise ImportFailure("invalid_input", "Title must contain 1–200 printable characters.")
    if title is not None:
        try:
            title.encode("utf-8")
        except UnicodeError as exc:
            raise ImportFailure("invalid_input", "Title contains invalid Unicode.") from exc
    exclude = data.get("exclude_notes", False)
    if not isinstance(exclude, bool):
        raise ImportFailure("invalid_input", "exclude_notes must be true or false.")
    return title.strip() if title else None, exclude


def private_write(path: Path, content: bytes):
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(content)


def save(request: Request, chapter: Chapter, source_url: str | None = None, title: str | None = None) -> dict:
    root = request.app.state.settings.data_dir
    relative = Path("uploads") / f"{uuid4().hex}.txt"
    path = root / relative
    try:
        private_write(path, chapter.text.encode("utf-8"))
        with connect(root / "metadata.sqlite3") as db:
            cursor = db.execute("INSERT INTO chapters (title, text_path, source_url) VALUES (?, ?, ?)", (title or chapter.title, relative.as_posix(), source_url))
            identifier = cursor.lastrowid
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"id": identifier, "title": title or chapter.title, "paragraphs": chapter.paragraphs, "characters": len(chapter.text), "status": "imported"}


@router.post("/text", status_code=201)
async def import_text(request: Request):
    data = await payload(request)
    title, exclude = options(data)
    chapter = text_chapter(field(data, "text"), exclude)
    return await run_in_threadpool(save, request, chapter, title=title)


@router.post("/upload")
async def import_upload(request: Request):
    filename = request.query_params.get("filename", "")
    extension = Path(filename).suffix.lower()
    if extension not in {".txt", ".epub", ".pdf"}:
        raise ImportFailure("unsupported_file", "Upload a PDF, UTF-8 TXT or EPUB file.")
    data = await body(request, MAX_TEXT_BYTES if extension == ".txt" else MAX_UPLOAD_BYTES)
    title, exclude = options({"title": request.query_params.get("title"), "exclude_notes": request.query_params.get("exclude_notes", "false") == "true"})
    if extension == ".txt":
        chapter = text_chapter(decode_text(data), exclude)
        return JSONResponse(await run_in_threadpool(save, request, chapter, title=title), status_code=201)
    if extension == ".pdf":
        chapter = await run_in_threadpool(pdf_chapter, data, filename, exclude)
        return JSONResponse(await run_in_threadpool(save, request, chapter, title=title), status_code=201)
    chapters = await run_in_threadpool(read_epub, data)
    upload_id = uuid4().hex
    await run_in_threadpool(private_write, request.app.state.settings.data_dir / "uploads" / f"{upload_id}.epub", data)
    return {"upload_id": upload_id, "chapters": [{"id": chapter.id, "title": chapter.title} for chapter in chapters], "status": "select_chapter"}


@router.post("/epub", status_code=201)
async def import_epub(request: Request):
    data = await payload(request)
    title, exclude = options(data)
    upload_id = field(data, "upload_id")
    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        raise ImportFailure("invalid_input", "Invalid EPUB upload identifier.")
    path = request.app.state.settings.data_dir / "uploads" / f"{upload_id}.epub"
    if not path.is_file():
        raise ImportFailure("missing_upload", "EPUB upload is no longer available. Upload it again.", 404)
    chapter_id = field(data, "chapter_id")

    def select():
        with path.open("rb") as stream:
            chapters = read_epub(stream.read(MAX_UPLOAD_BYTES + 1))
        selected = next((c for c in chapters if c.id == chapter_id), None)
        if selected is None:
            raise ImportFailure("invalid_chapter", "Select a chapter from this EPUB's reading order.")
        return epub_chapter(selected, exclude)

    chapter = await run_in_threadpool(select)
    return await run_in_threadpool(save, request, chapter, title=title)


@router.post("/url", status_code=201)
async def import_url(request: Request):
    data = await payload(request)
    title, exclude = options(data)
    chapter, url = await run_in_threadpool(ffn.fetch_chapter, field(data, "url"), exclude)
    return await run_in_threadpool(save, request, chapter, source_url=url, title=title)


@router.post("/notion", status_code=202)
async def narrate_notion(request: Request):
    data = await payload(request, 8192)
    identifier = notion.page_id(field(data, "url").strip())
    try:
        settings = GenerationSettings(voice=data.get("voice", "bf_emma"), speed=data.get("speed", 1.0))
    except TypeError as exc:
        raise JobFailure("invalid_settings", "Select a supported voice and narration speed.") from exc
    app = request.app.state

    def submit_notion():
        token = notion.read_token(app.settings.data_dir / "notion-token")
        if app.worker.backend is None:
            raise JobFailure("backend_unavailable", "Narration needs local voice models on your ChapterCast computer.", 503)
        chapter = notion.NotionPage(token, app.jobs.limits.max_characters).chapter(identifier)
        return app.jobs.enqueue_notion(chapter, f"https://www.notion.so/{identifier.hex}", settings)

    return await run_in_threadpool(submit_notion)
