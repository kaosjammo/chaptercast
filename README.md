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

Implementation issues will add verified setup, test, model-download and service instructions. See GitHub issues for current acceptance and evidence. Use content you are entitled to process; this application does not redistribute imported works.
