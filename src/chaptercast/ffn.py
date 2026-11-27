"""Single-chapter FFN fetch: allowlist, public DNS, pinned TLS and hard limits."""

import http.client
import re
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from chaptercast.http_transport import PinnedHTTPSConnection, interrupt_at, resolve_public as resolve_address
from chaptercast.imports import Chapter, ImportFailure, MAX_HTML_BYTES, html_paragraphs

HOSTS = {"fanfiction.net", "www.fanfiction.net", "m.fanfiction.net"}
FETCH_SECONDS = 20
MAX_REDIRECTS = 3
BLOCKED = "FanFiction.net blocked this request or returned a bot/interstitial page. Paste text or upload TXT/EPUB instead."


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 512 or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise ImportFailure("unsupported_url", "Use a public HTTPS FanFiction.net chapter URL.")
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname in HOSTS and parts.port in {None, 443}
                 and parts.username is None and parts.password is None and not parts.query and not parts.fragment
                 and re.fullmatch(r"/s/[1-9][0-9]{0,11}/[1-9][0-9]{0,5}(?:/[A-Za-z0-9_-]+)?/?", parts.path))
    except ValueError:
        valid = False
    if not valid:
        raise ImportFailure("unsupported_url", "Only HTTPS FanFiction.net /s/story/chapter URLs without queries or credentials are supported.")
    return f"https://{parts.hostname}{parts.path}"


def resolve_public(host: str, timeout: float) -> tuple[int, tuple]:
    try:
        return resolve_address(host, timeout)
    except OSError as exc:
        raise ImportFailure("ffn_unavailable", "Could not resolve FanFiction.net within the time limit. Paste/upload instead.", 502) from exc
    except ValueError as exc:
        raise ImportFailure("unsafe_url", str(exc)) from exc


def parse_chapter(html: str, exclude_notes: bool = False) -> Chapter:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "Imported FFN chapter"
    challenge = re.search(r"just a moment|attention required|verify you are human|checking your browser", title, re.I)
    if challenge or soup.select_one('#challenge-form, #cf-chl-widget, [id^="cf-chl"], script[src*="/cdn-cgi/challenge-platform/"]'):
        raise ImportFailure("ffn_blocked", BLOCKED, 502)
    roots = soup.select("#storytext")
    if len(roots) != 1:
        raise ImportFailure("ffn_blocked", BLOCKED, 502)
    text = html_paragraphs(roots[0], exclude_notes)
    return Chapter(text, title[:200])


def fetch_chapter(url: str, exclude_notes: bool = False) -> tuple[Chapter, str]:
    url = validate_url(url)
    deadline = time.monotonic() + FETCH_SECONDS
    for hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ImportFailure("ffn_unavailable", "FFN request timed out. Paste/upload instead.", 502)
        family, address = resolve_public(parts.hostname, min(3, remaining))
        conn = PinnedHTTPSConnection(parts.hostname, family, address, min(10, max(.01, deadline - time.monotonic())))

        try:
            with interrupt_at(conn, deadline):
                conn.request("GET", parts.path, headers={"User-Agent": "ChapterCast/0.1 (single chapter import)", "Accept": "text/html", "Accept-Encoding": "identity"})
                response = conn.getresponse()
                if response.status in {403, 429, 503}:
                    raise ImportFailure("ffn_blocked", f"FanFiction.net returned HTTP {response.status}. {BLOCKED}", 502)
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or hop == MAX_REDIRECTS:
                        raise ImportFailure("unsafe_redirect", "FFN redirect is missing or exceeds the redirect limit.")
                    url = validate_url(urljoin(url, location))
                    continue
                if response.status != 200:
                    raise ImportFailure("ffn_unavailable", "FFN chapter could not be retrieved. Paste/upload instead.", 502)
                if response.getheader("Content-Type", "").split(";")[0].strip().lower() != "text/html":
                    raise ImportFailure("invalid_response", "FFN returned an unsupported content type.", 502)
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise ImportFailure("invalid_response", "FFN returned an unsupported compressed response.", 502)
                declared = response.getheader("Content-Length")
                if declared is not None:
                    try:
                        length = int(declared)
                    except ValueError as exc:
                        raise ImportFailure("invalid_response", "FFN returned an invalid response length.", 502) from exc
                    if length < 0 or length > MAX_HTML_BYTES:
                        raise ImportFailure("too_large", "FFN response exceeds the 2 MiB limit.", 413)
                data = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ImportFailure("ffn_unavailable", "FFN request timed out. Paste/upload instead.", 502)
                    live_socket = getattr(conn, "_live_socket", None) or conn.sock
                    if live_socket:
                        live_socket.settimeout(min(10, remaining))
                    chunk = response.read1(min(65536, MAX_HTML_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > MAX_HTML_BYTES:
                        raise ImportFailure("too_large", "FFN response exceeds the 2 MiB limit.", 413)
                if declared is not None and len(data) != length:
                    raise ImportFailure("invalid_response", "FFN returned an incomplete page.", 502)
                try:
                    html = data.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ImportFailure("invalid_response", "FFN returned invalid UTF-8 HTML.", 502) from exc
                return parse_chapter(html, exclude_notes), url
        except (OSError, http.client.HTTPException) as exc:
            raise ImportFailure("ffn_unavailable", "FFN request failed or timed out. Paste/upload instead.", 502) from exc
        finally:
            conn.close()
    raise ImportFailure("unsafe_redirect", "FFN redirect limit exceeded.")
