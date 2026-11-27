"""Extract selectable PDF text locally with bounded Poppler subprocesses."""

import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
from threading import BoundedSemaphore
from time import monotonic

from chaptercast.imports import Chapter, ImportFailure, MAX_TEXT_BYTES, MAX_UPLOAD_BYTES, text_chapter


_PDF_SLOTS = BoundedSemaphore(2)
_TIMEOUT = 12
_MEMORY_LIMIT = 512 * 1024 * 1024
_DIAGNOSTIC_LIMIT = 64 * 1024
_POPPLER_ERROR = re.compile(rb"(?m)^[A-Za-z /]*Error(?: \([^\n]*\))?:")
_IMAGE_PAGE = re.compile(rb"(?m)^\s*(\d+)\s+\d+\s+(?:image|mask|smask|stencil)\s")

# Set limits in a fresh process. A preexec_fn can deadlock the threaded server.
_LIMIT_AND_EXEC = """
import os
import resource
import sys
limits = resource.getrlimit(resource.RLIMIT_AS)
limit = min([int(sys.argv[1])] + [value for value in limits if value != resource.RLIM_INFINITY])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
os.execv(sys.argv[2], sys.argv[2:])
"""


def _unreadable() -> ImportFailure:
    return ImportFailure("unreadable_pdf", "Could not read this PDF completely. Check that it opens and is unlocked, or paste the text instead.")


def _convert(source, converter: str, args: list[str], deadline: float) -> bytes:
    source.seek(0)
    command = [sys.executable, "-I", "-c", _LIMIT_AND_EXEC, str(_MEMORY_LIMIT), converter, *args]
    with subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={**os.environ, "LC_ALL": "C"}) as process:
        output = bytearray()
        diagnostics = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, (output, MAX_TEXT_BYTES))
                selector.register(process.stderr, selectors.EVENT_READ, (diagnostics, _DIAGNOSTIC_LIMIT))
                while selector.get_map():
                    remaining = deadline - monotonic()
                    if remaining <= 0 or not (ready := selector.select(remaining)):
                        raise subprocess.TimeoutExpired(command, _TIMEOUT)
                    for key, _ in ready:
                        buffer, limit = key.data
                        chunk = os.read(key.fd, min(65536, limit + 1 - len(buffer)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer.extend(chunk)
                        if len(buffer) > limit:
                            if buffer is diagnostics:
                                raise _unreadable()
                            raise ImportFailure("too_large", "Extracted PDF content exceeds the 1 MiB limit. Upload a shorter PDF.", 413)
            # Poppler can exit successfully after dropping a damaged page.
            if process.wait(timeout=max(0, deadline - monotonic())) != 0 or _POPPLER_ERROR.search(diagnostics):
                raise _unreadable()
        finally:
            if process.poll() is None:
                process.kill()
        return bytes(output)


def _extract(data: bytes, pdftotext: str, pdfimages: str) -> str:
    deadline = monotonic() + _TIMEOUT
    # Use an anonymous local file for input. Converters never see the upload name.
    with tempfile.TemporaryFile() as source:
        source.write(data)
        raw = _convert(source, pdftotext, ["-enc", "UTF-8", "-eol", "unix", "-", "-"], deadline)
        pages = [page.strip() for page in raw.decode("utf-8").removesuffix("\f").split("\f")]
        if not any(pages):
            raise ImportFailure("pdf_no_text", "This PDF has no selectable text. Run OCR on the scan or paste the text instead.")

        blank = {number for number, page in enumerate(pages, 1) if not page}
        if blank:
            images = _convert(source, pdfimages, ["-list", "-"], deadline)
            scans = sorted(blank.intersection(map(int, _IMAGE_PAGE.findall(images))))
            if scans:
                numbers = ", ".join(map(str, scans[:8]))
                if len(scans) > 8:
                    numbers += ", …"
                label, verb = ("page", "has") if len(scans) == 1 else ("pages", "have")
                raise ImportFailure("pdf_needs_ocr", f"PDF {label} {numbers} {verb} images with no selectable text. Run OCR on the PDF or paste the text instead.")
        return "\n\n".join(page for page in pages if page)


def pdf_chapter(data: bytes, filename: str, exclude_notes: bool) -> Chapter:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImportFailure("too_large", "PDF uploads must be 10 MiB or smaller.", 413)
    if b"%PDF-" not in data[:1024]:
        raise ImportFailure("unreadable_pdf", "Choose a valid PDF file.")

    pdftotext = shutil.which("pdftotext")
    pdfimages = shutil.which("pdfimages")
    if not pdftotext or not pdfimages:
        raise ImportFailure("pdf_unavailable", "PDF import needs Poppler on your ChapterCast computer. You can still paste the text.", 503)
    if not _PDF_SLOTS.acquire(blocking=False):
        raise ImportFailure("pdf_busy", "Other PDFs are being read. Try again in a moment.", 503)
    try:
        text = _extract(data, pdftotext, pdfimages)
    except subprocess.TimeoutExpired:
        raise ImportFailure("pdf_timeout", "Reading this PDF took too long. Try a shorter PDF or paste the text.") from None
    except UnicodeError:
        raise _unreadable() from None
    except OSError:
        raise ImportFailure("pdf_unavailable", "PDF import is unavailable on your ChapterCast computer. You can still paste the text.", 503) from None
    finally:
        _PDF_SLOTS.release()

    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    title = "".join(c if c.isprintable() else " " for c in Path(basename).stem)
    return text_chapter(text, exclude_notes, title.strip()[:200] or "Imported PDF", reject_html=False)
