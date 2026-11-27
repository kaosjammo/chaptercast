"""Extract selectable PDF text locally with bounded Poppler subprocesses."""

import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
from threading import BoundedSemaphore
from time import monotonic

from chaptercast.imports import Chapter, ImportFailure, MAX_TEXT_BYTES, MAX_UPLOAD_BYTES, text_chapter


_PDF_SLOTS = BoundedSemaphore(2)
_TIMEOUT = 12


def _extract(data: bytes, converter: str) -> bytes:
    # A private, anonymous file lets Poppler read the upload without a pipe
    # deadlock or a user-controlled path. Only bounded text leaves the process.
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        command = [converter, "-q", "-enc", "UTF-8", "-eol", "unix", "-", "-"]
        with subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            deadline = monotonic() + _TIMEOUT
            output = bytearray()
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise subprocess.TimeoutExpired(command, _TIMEOUT)
                        chunk = os.read(process.stdout.fileno(), min(65536, MAX_TEXT_BYTES + 1 - len(output)))
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > MAX_TEXT_BYTES:
                            raise ImportFailure("too_large", "PDF text exceeds the 1 MiB limit. Upload a shorter PDF.", 413)
                if process.wait(timeout=max(0, deadline - monotonic())) != 0:
                    raise ImportFailure("unreadable_pdf", "Could not read this PDF. Check that it opens and is unlocked, then try again.")
            finally:
                if process.poll() is None:
                    process.kill()
            return bytes(output)


def pdf_chapter(data: bytes, filename: str, exclude_notes: bool) -> Chapter:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImportFailure("too_large", "PDF uploads must be 10 MiB or smaller.", 413)
    if b"%PDF-" not in data[:1024]:
        raise ImportFailure("unreadable_pdf", "Choose a valid PDF file.")

    converter = shutil.which("pdftotext")
    if converter is None:
        raise ImportFailure("pdf_unavailable", "PDF import needs Poppler on your ChapterCast computer. You can still paste the text.", 503)
    if not _PDF_SLOTS.acquire(blocking=False):
        raise ImportFailure("pdf_busy", "Other PDFs are being read. Try again in a moment.", 503)
    try:
        raw = _extract(data, converter)
    except subprocess.TimeoutExpired:
        raise ImportFailure("pdf_timeout", "Reading this PDF took too long. Try a shorter PDF or paste the text.") from None
    except OSError:
        raise ImportFailure("pdf_unavailable", "PDF import is unavailable on your ChapterCast computer. You can still paste the text.", 503) from None
    finally:
        _PDF_SLOTS.release()

    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ImportFailure("unreadable_pdf", "Could not read the text in this PDF. Try pasting the text instead.") from None
    if not text.strip():
        raise ImportFailure("pdf_no_text", "This PDF has no selectable text. Run OCR on the scan or paste the text instead.")

    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    title = "".join(c if c.isprintable() else " " for c in Path(basename).stem)
    pages = "\n\n".join(page.strip() for page in text.split("\f") if page.strip())
    return text_chapter(pages, exclude_notes, title.strip()[:200] or "Imported PDF")
