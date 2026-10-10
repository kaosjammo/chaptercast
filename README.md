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
tables for chapters, jobs, chunk checkpoints and audio outputs. Text, TXT, EPUB
chapter selection and bounded FanFiction.net chapter import are implemented. A
durable server-owned queue is implemented behind a synthesis/encoding adapter.
Real Kokoro narration and encoding are reserved for issue #4; the default app
keeps submitted jobs queued until that backend is provided. Playback remains a
later issue.

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
TTS service or external integration. Queue publication tests use original
synthetic PCM with real FFmpeg/ffprobe when those executables are installed;
those tests explicitly skip when they are unavailable. They do not prove Kokoro
narration. CI runs this suite on Python 3.11 and 3.12.
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
data directory. Schema version 2 uses SQLite foreign keys, constrained job
states/output formats and chunk checkpoints. Version 1 migrates transactionally:
imports and legacy output records are retained; old queued/running jobs become
failed with `legacy_job_not_resumable` because they have no content/settings
snapshot. Legacy outputs have no validation manifest and are not served. A newer
schema version fails startup rather than being overwritten.

Keep private content, credentials, models, generated audio, browser evidence and
logs in ignored `.runtime/` or `.evidence/` paths. If you override the data
directory, keep it outside Git or add a local ignore rule before using it. Do not
commit real chapter contents or device/host/authentication details. The tests use
only original, labelled synthetic text, EPUB and HTML fixtures.

Private Tailscale hosting and service activation are reserved for issue #8. These
development commands do not configure Serve, Funnel or any live service. Later
issues will add model-download and deployment instructions. Use content you are
entitled to process; this application does not redistribute imported works.

## Chapter import (issue #2)

Use **Import a chapter** in the browser shell. Choose pasted text, a UTF-8 TXT
upload, an EPUB upload or a supported FanFiction.net URL. EPUB upload first lists
chapters in spine reading order; select one and press **Import selected chapter**.
Only that chapter is saved. Imported metadata shown in the shell is for the
current browser session; the persistent library is reserved for issue #5.
Imports do not enqueue narration jobs.

Blank lines preserve text paragraph boundaries; single line breaks remain within
paragraphs. HTML block elements become paragraphs and `<br>` becomes a line break.
FFN extraction reads only `#storytext`, excluding site navigation and inactive
content. EPUB extraction reads the selected XHTML body, excluding navigation and
inactive content. No embedded scripts, resources or links are executed or fetched.

**Exclude recognised author notes** removes paragraphs starting `A/N:` or
`Author's Note:` (including a curly apostrophe), plus EPUB elements explicitly
marked as footnotes, endnotes, rearnotes or annotations. It is a conservative
label-based filter: unlabelled notes and continuation paragraphs remain. Check or
edit your source if more precise exclusion is needed. Headings and story paragraph
boundaries are retained; this does not attempt to infer where unlabelled prose ends.

Limits are fixed to keep import bounded:

| Input | Limit |
| --- | --- |
| Normalised chapter text / TXT upload | 1 MiB UTF-8 |
| Pasted JSON request envelope | 2 MiB |
| EPUB upload | 10 MiB |
| EPUB archive | 512 members, 32 MiB total expanded, 10 MiB per member, 200:1 ratio |
| EPUB metadata or individual XHTML chapter | 2 MiB |
| FFN response | 2 MiB HTML, 20 seconds total, 3 redirects |

EPUB supports unencrypted ZIP stored/deflated members and local XHTML spine
chapters (EPUB 2/3). Duplicate/unsafe member paths, symlinks, encryption, bad CRCs,
missing metadata/spine references, malformed XHTML and XML entities are rejected.
Members are validated without filesystem extraction. Navigation and non-linear
spine items are not offered as chapters. Unsupported spine formats fail explicitly.

Supported URL hosts are `fanfiction.net`, `www.fanfiction.net` and
`m.fanfiction.net`, using HTTPS on port 443 and `/s/<story>/<chapter>[/<slug>]`.
Story and chapter numbers must be positive; slugs use letters, digits, hyphens or
underscores. Queries, credentials, fragments, other hosts and other URL types are
rejected. Each redirect passes the same validation and fresh public-only DNS
checks. The TLS connection pins the checked numeric address while validating the
FFN hostname, avoiding a second DNS lookup or environment proxies. Mixed public
and non-public answers, private/reserved addresses and IPv6 transition addresses
are rejected. DNS waits are capped at 3 seconds, with at most two outstanding
resolver calls; socket operations and an overall deadline bound the network fetch.

HTTP 403/429/503, challenge pages and pages without a single story container
return `ffn_blocked` with paste/upload fallback. Other network/content failures
also fail explicitly. Synthetic fixture extraction tests establish parser and
transport behaviour, **not live FFN availability**. A blocked live request does
not make paste, TXT or EPUB import unavailable.

Import routes:

- `POST /api/import/text`: JSON `text`, optional `title`, `exclude_notes`.
- `POST /api/import/upload?filename=original.txt`: raw UTF-8 file bytes; optional
  `title` and `exclude_notes=true` query parameters. Use `.epub` for raw EPUB bytes;
  the response contains an upload identifier and chapter choices.
- `POST /api/import/epub`: JSON `upload_id`, `chapter_id`, optional `title`,
  `exclude_notes`.
- `POST /api/import/url`: JSON `url`, optional `title`, `exclude_notes`.

Successful imports return metadata only, with HTTP 201. EPUB selection returns
HTTP 200 before import. Errors return a stable `code` and a readable `message`
without echoing submitted text. Normalised text and EPUB uploads use random names
under the private data directory's `uploads/`; they are not served as static files.
Original EPUB uploads remain local for subsequent chapter selection. Failed text
saves roll back and remove the text file. FFN source HTML is never persisted.
No imported content is published by these routes.

## Durable queue (issue #3)

Submit an imported chapter with `POST /api/jobs`, for example:

```json
{"chapter_id": 1, "voice": "bf_emma", "speed": 1.0, "formats": ["opus", "m4a"]}
```

The API returns HTTP 202 with a persisted job identifier. Poll `GET /api/jobs/{id}`
for status, completed/total chunks, progress and output metadata. States are
`queued`, `running`, `completed`, `failed` and `cancelled`. Progress measures chunk
synthesis; it may reach 1 while encoding/validation is still running. Only
`completed` makes outputs available. `POST /api/jobs/{id}/cancel` cancels queued
or running work; completed/failed/cancelled jobs are terminal. A new submission
can create a fresh job after failure/cancellation. No request, browser connection
or page owns the worker, so disconnecting does not cancel a submitted job.

Supported voice identifiers are `bf_emma` (default), `bf_isabella`, `bm_george` and
`bm_lewis`; speed is 0.5–2.0. Formats may contain Opus, M4A or both (default).
Canonical cache keys cover normalised content, voice, speed, requested formats,
chunking and pipeline version. Matching queued/running/completed submissions
reuse the existing job, including submissions from another identical import.
Cancelled/failed jobs are not reused. Missing or changed completed files invalidate
the cache. Queue settings and every chunk's original text are snapshotted in
SQLite, so recovery does not reread a changed upload.

One server-owned thread holds an OS process lock in `worker.lock` for its entire
lifetime. A second app using the same local data directory can submit and poll,
but cannot claim work. The lock has no expiry; process death releases it. Restart
requeues interrupted running jobs, verifies completed WAV checkpoints against
their stored hashes, removes partial/uncommitted files and resumes only missing
or damaged chunks. Intact completed chunks are not synthesised again. Graceful
shutdown requeues interrupted work with checkpoints intact. This requires a
local filesystem with SQLite transactions, POSIX locks and atomic directory
rename; network/shared multi-host storage is unsupported.

Chunks prefer English sentence/paragraph boundaries, recognise a small set of
common abbreviations and preserve every character of normalised text in order.
Long sentences split at whitespace, then at a character boundary for long tokens.
This is a conservative heuristic, not a full linguistic parser.

The worker independently validates mono PCM16 chunks. Every requested final
format must have the expected container/codec, positive duration covering the
ordered chunks, bounded bytes and a successful full FFmpeg decode. All output
files are flushed and renamed together into a private ready directory, then all
metadata and the completed state commit in one SQLite transaction. Cancellation
competes with that same transaction. Files from a crash before commit remain
invisible and are reclaimed on recovery. Downloads at
`GET /api/jobs/{id}/outputs/{opus|m4a}` require completed state and intact hashes;
private work directories and uploads are never static routes.

Generation admission and execution limits are fixed:

| Limit | Value |
| --- | --- |
| Submission JSON | 8 KiB |
| Normalised text | 100,000 characters, within the import's 1 MiB UTF-8 limit |
| Chunk size/count | 1,000 characters / 256 chunks |
| Active jobs | 4 (including cancelled work still settling) |
| Persisted job history | 100 jobs |
| Generation artifacts | 256 MiB per job, 2 GiB aggregate reservation/accounting |
| Individual PCM chunk | 8 MiB, at most 300 seconds |
| Complete audio | At most 3,600 seconds |
| Worker attempt | 1,800 seconds, checked between operations and by the adapter |
| Validation subprocess | 30 seconds probe / 60 seconds full decode per format |

Active jobs reserve their complete artifact allowance; terminal jobs retain their
actual artifact accounting. Four maximum active job reservations total 1 GiB.
One hour of mono 24 kHz PCM16 checkpoints requires 172,800,000 bytes before WAV
headers; nominal 32 kbps Opus plus 48 kbps AAC adds roughly 36,000,000 bytes.
The 256 MiB job budget accommodates these checkpoints and outputs with room for
container overhead and bounded temporary work. Cancellation does not release a
live worker's reservation before it settles. These are generation quotas, not
quotas on import uploads or model downloads. No automatic deletion of private library data is
implemented; a full history requires deliberate local maintenance while stopped.

`GET /api/jobs/worker` reports `backend_ready` and `worker_owned`. In this issue
`backend_ready` is false for the default application: jobs persist but real
narration is unavailable. Issue #4 will provide the `Backend` adapter in
`worker.py`, honouring cooperative cancellation, write budgets and bounded model
and encoding operations. It must encode directly from the ordered WAV
checkpoints or a bounded stream, without creating a second full-size concatenated
PCM copy on disk. `max_bytes` covers peak additional disk usage across all output
destinations and temporary work combined; the worker has already subtracted the
existing checkpoints from the remaining encoding budget. Its pipeline version
must change when model/rendering behaviour changes. Queue tests use labelled
synthetic tones with real FFmpeg; they establish queue/validation behaviour, not
live TTS, physical-phone behaviour
or deployment acceptance.
