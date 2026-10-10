# Local operating instructions

ChapterCast is a single-user private application. The public repository contains
source, original test fixtures and app icons. Keep chapters, books, models,
audio, SQLite, backups, logs, certificates and browser evidence in ignored paths.
There is no application login or per-user authorisation. Loopback binding and
browser request guards do not replace private-network access controls.

## Install and start

Use Python 3.11 or 3.12, the existing `uv` executable and system FFmpeg/ffprobe
with `libopus` and AAC encoders. `uv sync` creates the isolated `.venv`. Follow
the [README model setup](../README.md#local-narration-and-export-issue-4) once;
keep the pinned weights, British voices and provenance private. No GPU is needed.

```sh
export UV_CACHE_DIR="$PWD/.runtime/uv-cache"
export UV_PYTHON_DOWNLOADS=never
uv sync --locked --extra dev --extra tts --python 3.11
uv run --locked --extra tts chaptercast
```

If `uv` is missing from PATH, invoke its existing absolute path. Do not install a
second copy. In a sandbox, an ordinary blocked dependency domain requires the
supported narrow per-command approval through the same execution tool. Stop if
approval is rejected; retain the current sandbox/model/settings.

Open `http://127.0.0.1:8000/`. Stop the foreground server with Ctrl+C. This does
not install a service or change Tailscale. Run one server/queue worker per local
data directory. SQLite and process locks require a local filesystem.

Check `/health` for application availability and `/api/jobs/worker` for
`backend_ready` and `worker_owned`. A healthy app can have no narration backend.
The CPU renderer uses at most four inference threads and one interop thread.
Application commands do not change CPU power governors, fan controls or system
hardware settings.

## Import, narrate and listen

1. Paste plain text or upload UTF-8 TXT/EPUB. EPUB upload offers spine chapters;
   select one. Only that chapter is imported. Review optional note exclusion.
2. Choose a British voice and narration speed. Preview uses original test prose;
   `bf_emma`, speed 1.0 and both formats are the defaults.
3. Press **Generate audio** for a library chapter. Closing the tab does not
   cancel work. The queue shows progress, cancellation and errors. Chunk
   progress can reach 100% while encoding is still running.
4. Play the completed chapter, seek or change playback rate. Resume belongs to
   this browser and chapter; private browsing or clearing site storage can erase it.
5. Save **Download M4A** or **Download OPUS**, wait for completion, then open the
   file from Downloads in a compatible external player. That complete file
   works without the server. The app's cached interface contains no audio.

FFN URL import supports one allowed chapter URL with bounded public-address
resolution, pinned TLS and revalidated redirects. HTTP 403/429/503 or bot pages
fail with paste/upload fallback; never paste the bot HTML into narration.
Fixture success is not live source success. Record live failures with date,
status/code and fallback outcome in private evidence; do not preserve real story
content in public source. No browser, proxy or credential bypass is supported.

## Request and hosting boundaries

Request Host headers default to loopback names only. Browser writes reject a
foreign/null Origin or `Sec-Fetch-Site` other than `same-origin`/`none`. Frames
are denied. Headerless local CLI clients remain supported. These guards protect
against browser cross-site writes and hostile hostnames; they do not authenticate
local programs or users with access to an explicitly trusted private host.

`CHAPTERCAST_TRUSTED_HOSTS` optionally lists comma-separated exact lowercase
hostnames without schemes, ports, paths or wildcards. It changes HTTP Host
validation only; it never changes the loopback bind. Leave it empty for local
use. The controller must privately set the approved HTTPS hostname when issue #8
authorises hosting. An approved reverse proxy must preserve Host and the correct
scheme so same-origin browser writes succeed. Keep proxy trust restricted to the
local proxy; do not enable arbitrary forwarded-header trust.

No public bind, Funnel, route replacement or live activation is authorised by
issue #7. Portable service/deployment preparation and owning-controller
activation belong to issue #8; follow the [deployment runbook](DEPLOYMENT.md).

The service worker caches only fixed shell resources. Offline library access,
imports, queue mutations, narration, streaming and new downloads are unavailable.
There is no mutation replay. Loopback HTTP service-worker checks do not prove
HTTPS hosting. Installation, Android external-player opening, lock-screen
controls and screen-off continuity need actual device qualification.

## Recovery and maintenance

On orderly shutdown, interrupted work returns to the queue with completed WAV
checkpoints retained. After process death, restart using the same data directory:
the OS releases the worker lock, recovery verifies checkpoint hashes, discards
partial/uncommitted artifacts and resumes missing/damaged chunks. Only complete,
validated bundles are downloadable. A failed/cancelled job can be submitted
again; it is not a valid cache hit. Do not edit SQLite or WAV checkpoints while
the server is running, or delete `worker.lock` to start a competing worker.

If `backend_ready` is false, check the TTS extra, FFmpeg/ffprobe and local pinned
model provenance. A standard dev-only `uv run` may remove optional TTS packages;
resynchronise with `--extra tts` before starting narration. Missing/corrupt model
files make readiness fail. Restore verified private files or explicitly repeat
model setup; do not substitute a different model under an old pipeline identity.

If the worker reports `worker_failed`, preserve private logs, stop the process
and restart once to run recovery. If failure repeats, preserve the data and
investigate; do not label an incomplete job completed. The attempt, chunk and
encoder deadlines bound individual work; errors reveal stable codes, not prose.

Generation allows four active jobs, 100 historical jobs, 256 MiB per job and
2 GiB aggregate reservations/artifact accounting. Checkpoints, compact outputs
and temporary encoding work count together. Uploads and models have separate
storage needs and no aggregate automatic cleanup. There is no library-delete UI.
If history/storage is full, stop, back up and deliberately maintain the local
library; a fresh ignored data directory gives an empty library while retaining
the old backup. Keep model provenance and files together. Do not manually drop
metadata rows as a routine quota workaround.

For a consistent backup, stop the server and copy the complete data directory
(SQLite, uploads, audio and model provenance/files) into an ignored/off-repository
private backup. Protect the backup like the original content. Restore it while
stopped, then start with the same data-directory setting and inspect readiness,
library and downloads. Browser resume settings are separate from the server
backup. Browser downloads are additional private copies; clearing server data
does not remove them.

## Qualification and handoff

```sh
uv run --extra dev pytest -q
python scripts/audit_public.py > .evidence/public-audit.json
```

Create `.evidence/` before redirecting. The tests include real process crash
recovery with synthetic PCM and FFmpeg; they skip codec checks if FFmpeg is
absent. Node contract tests skip if Node is absent. Skips are not acceptance.
The publication tripwire scans reachable history and current public files for
common private artifacts/paths/tokens; manually review fixture authorship,
copyright, unusual identity data and unrecognised secrets too.

With dev+tts installed and task-owned copies of verified private models, run:

```sh
uv run --extra dev --extra tts python scripts/verify_release.py \
  --retained-chapter "$RETAINED_ORIGINAL_CHAPTER_DIR" \
  --retained-receipt "$RETAINED_ACCEPTANCE_JSON" \
  --models .runtime/models
```

The retained inputs must be the original issue #4 acceptance chapter and receipt.
The harness checks unchanged rendering-source hashes/pipeline, copies the data,
generates one fresh CPU British preview, checks both codecs and real chapter
downloads/ranges, shuts down the application and decodes saved complete files.
It records exact paths, bytes, hashes, duration, CPU coverage and source identity
under `.evidence/issue-7/`. Long chapter generation is explicitly **reused**;
the source evidence is read-only. It does not claim TCP/browser/device acceptance.

On the exact final commit, run tests and audit, then use native T3 preview on a
temporary loopback server for real playback, seeking, rate, per-chapter reload
resume and narrow layout. Record screenshot-tool failures separately from
DOM/actions. Complete saved downloads must decode after server shutdown.
Retain command argv/exits, source seals, real-source observations and limitations
privately. One combined independent read-only review covers the exact candidate,
with at most three remediation cycles and no additional source writers. The
controller verifies the final head and decides publication/issue closure.
