# ChapterCast

A private chapter-to-audiobook tool: submit text or a supported chapter URL from a phone, generate narration on a desktop, and play or download a compact complete chapter.

**Status:** implementation in progress. No deployed service or working narration is claimed yet.

## MVP architecture

- Python FastAPI backend and a small responsive browser interface.
- SQLite metadata and durable single-worker generation queue.
- Kokoro local TTS, initially CPU-first with bounded threads and a British English voice.
- FFmpeg mono Opus at a nominal 32 kbps, plus AAC/M4A for compatible players.
- Loopback-bound desktop service, privately exposed using Tailscale Serve HTTPS.
- Downloadable complete chapter files; downloaded playback does not require server connectivity.

The repository is public. Chapter text, uploaded books, narration outputs, models, credentials, runtime state and device-specific deployment evidence remain local and are not distributed in this repository.

## Sequential backlog

1. [Application foundation and tests](../../issues/1)
2. [Safe text, TXT/EPUB and chapter-URL import](../../issues/2)
3. [Durable single-worker queue and recovery](../../issues/3)
4. [Real Kokoro narration and compact audio](../../issues/4)
5. [Responsive library and player](../../issues/5)
6. [Installable shell and downloaded playback](../../issues/6)
7. [Safety and end-to-end verification](../../issues/7)
8. [Private desktop/Tailscale deployment](../../issues/8)

## Important limitations

FanFiction.net may block automated requests. Blocked pages must fail explicitly, never be narrated. Paste/upload remains available; successful tests with local HTML fixtures do not establish successful live website import.

A native Android client, whole-story crawling, character-specific voices and paid cloud narration are outside the first version. Browser media and offline behaviour must be tested and reported honestly; a manifest or mobile viewport alone does not prove physical-phone screen-off playback.

## Development and operation

The foundation provides a health route, a static mobile shell and SQLite metadata
tables for chapters, jobs and audio outputs. Import, queue execution, Kokoro
narration, audio encoding and playback are later issues; none are implemented yet.

Use Python **3.11 or 3.12** and [uv](https://docs.astral.sh/uv/). From the repository
root, install the locked dependencies in the project's `.venv`:

```sh
export UV_CACHE_DIR="$PWD/.runtime/uv-cache"
export UV_PYTHON_DOWNLOADS=never
uv sync --locked --extra dev --python 3.11
uv run --extra dev pytest -q
```

The `dev` extra contains the unit/API test dependencies. Tests use temporary
databases and in-process HTTP requests; they need no credentials, models, FFmpeg,
TTS service or external integration. CI runs this suite on Python 3.11 and 3.12.
Dependency installation requires access to PyPI. If `uv` is outside your PATH,
use its existing absolute executable path rather than installing a duplicate.

Start a foreground local development server (this does not install or activate a
service):

```sh
uv run chaptercast
# Equivalent: uv run python -m chaptercast
```

Open `http://127.0.0.1:8000/` for the shell. `GET /health` returns
`{"status":"ok"}` after startup initialises the database. Stop with Ctrl+C.
The health response establishes application availability, not narration readiness.

Configuration is read from environment variables. `.env.example` documents them;
the application does **not** automatically load `.env` files:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHAPTERCAST_HOST` | `127.0.0.1` | Loopback bind address; non-loopback values are rejected. |
| `CHAPTERCAST_PORT` | `8000` | Local TCP port, 1–65535. |
| `CHAPTERCAST_DATA_DIR` | `.runtime` | Private data directory, relative to the working directory or absolute. |

Startup creates `metadata.sqlite3`, `uploads/`, `models/` and `audio/` beneath the
data directory. Schema version 1 uses SQLite foreign keys and constrained job
states and output formats. A newer schema version fails startup rather than being
overwritten. This schema does not yet implement a durable queue.

Keep private content, credentials, models, generated audio, browser evidence and
logs in ignored `.runtime/` or `.evidence/` paths. If you override the data
directory, keep it outside Git or add a local ignore rule before using it. Do not
commit real chapter contents or device/host/authentication details. The tests use
only original, labelled metadata fixtures.

Private Tailscale hosting and service activation are reserved for issue #8. These
development commands do not configure Serve, Funnel or any live service. Later
issues will add model-download and deployment instructions. Use content you are
entitled to process; this application does not redistribute imported works.
