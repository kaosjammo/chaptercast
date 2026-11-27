"""Read a single shared Notion page through Notion's fixed, read-only API."""

from http.client import HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
import stat
from time import monotonic, sleep
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

from chaptercast.imports import Chapter, ImportFailure, text_chapter

API_HOST = "api.notion.com"
API_VERSION = "2026-03-11"
PAGE_ID = r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
PAGE_PATH_ID = re.compile(rf"(?:^|-)({PAGE_ID})$", re.I)
TEXT_BLOCKS = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "to_do", "quote", "callout", "toggle",
}
NESTED_BLOCKS = TEXT_BLOCKS | {"column_list", "column", "synced_block", "table"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUESTS = 64
MAX_BLOCKS = 1500
MAX_DEPTH = 12


def page_id(link: str) -> UUID:
    invalid = ImportFailure("invalid_notion_link", "Paste a Notion page link copied from Share → Copy link.")
    if len(link) > 2048 or any(char.isspace() or ord(char) < 32 for char in link):
        raise invalid
    try:
        target = urlsplit(link)
        host = target.hostname or ""
        if (target.scheme != "https" or target.username is not None or target.password is not None
                or target.port not in (None, 443)
                or not (host in {"notion.so", "www.notion.so", "app.notion.com", "notion.site"} or host.endswith(".notion.site"))):
            raise invalid
        # A database view's open-page link identifies the row in ?p=.
        page = parse_qs(target.query, keep_blank_values=True).get("p")
        if page is not None:
            if len(page) != 1 or not re.fullmatch(PAGE_ID, page[0], re.I):
                raise invalid
            return UUID(page[0])
        match = PAGE_PATH_ID.search(target.path.rstrip("/").rsplit("/", 1)[-1])
        if not match:
            raise invalid
        return UUID(match[1])
    except ValueError as exc:
        raise invalid from exc


def read_token(path: Path) -> str:
    """Read only a private regular file. Never echo the token or file contents."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("permissions")
            token = stream.read(258).strip().decode("ascii")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
                raise ValueError("token")
    except (OSError, ValueError) as exc:
        raise ImportFailure(
            "notion_not_configured",
            "Notion is not connected. Set up the read-only connection on your ChapterCast computer, then try again.",
            503,
        ) from exc
    return token


def response_error() -> ImportFailure:
    return ImportFailure("notion_unavailable", "Could not finish reading this page from Notion. Try again in a moment.", 502)


def page_limit() -> ImportFailure:
    return ImportFailure("notion_page_limit", "This Notion page is too large or deeply nested to narrate in one go. Try a smaller page.", 413)


def rich_text(parts: list) -> str:
    return "".join(part["plain_text"] for part in parts)


class NotionPage:
    def __init__(self, token: str, max_characters: int):
        self.token = token
        self.max_characters = max_characters
        self.deadline = monotonic() + 20
        self.requests = 0
        self.blocks = 0
        self.characters = 0
        self.parts: list[str] = []

    def get(self, path: str, query: dict | None = None) -> dict:
        for attempt in range(2):
            self.requests += 1
            if self.requests > MAX_REQUESTS:
                raise page_limit()
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise response_error()
            connection = HTTPSConnection(API_HOST, timeout=min(5, remaining))
            try:
                # The pasted URL contributes only a validated UUID. Redirects are never followed.
                connection.request("GET", path + (f"?{urlencode(query)}" if query else ""), headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": API_VERSION,
                    "Accept": "application/json",
                })
                response = connection.getresponse()
                if response.status == 401:
                    raise ImportFailure("notion_invalid_token", "Update the Notion connection on your ChapterCast computer, then try again.", 503)
                if response.status in (403, 404):
                    raise ImportFailure("notion_page_access", "Check the page link and add your ChapterCast connection to the page in Notion.", 404)
                if response.status == 429:
                    delay = response.getheader("Retry-After") or ""
                    if attempt == 0 and delay in {"1", "2", "3", "4", "5"} and monotonic() + int(delay) < self.deadline:
                        connection.close()
                        sleep(int(delay))
                        continue
                    raise ImportFailure("notion_busy", "Notion is receiving too many requests. Wait a few seconds, then try again.", 429)
                if response.status != 200:
                    raise response_error()
                raw = bytearray()
                while True:
                    if monotonic() >= self.deadline:
                        raise response_error()
                    piece = response.read1(65536)
                    if not piece:
                        break
                    raw.extend(piece)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise page_limit()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise response_error()
                return result
            except (OSError, HTTPException, ValueError, RecursionError) as exc:
                raise response_error() from exc
            finally:
                connection.close()

    def children(self, block_id: UUID, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise page_limit()
        query: dict[str, int | str] = {"page_size": 100}
        cursors = set()
        while True:
            result = self.get(f"/v1/blocks/{block_id}/children", query)
            if result["object"] != "list" or not isinstance(result["results"], list):
                raise response_error()
            for block in result["results"]:
                self.blocks += 1
                if self.blocks > MAX_BLOCKS:
                    raise page_limit()
                kind = block["type"]
                if kind in TEXT_BLOCKS:
                    self.append(rich_text(block[kind]["rich_text"]))
                elif kind == "table_row":
                    self.append(". ".join(text for cell in block[kind]["cells"] if (text := rich_text(cell)).strip()))
                # Read text in containers, but do not follow child pages, linked databases or media.
                if block["has_children"] and kind in NESTED_BLOCKS:
                    self.children(UUID(block["id"]), depth + 1)
            if result["has_more"] is False:
                return
            cursor = result["next_cursor"]
            if result["has_more"] is not True or not isinstance(cursor, str) or not 0 < len(cursor) <= 256 or cursor in cursors:
                raise response_error()
            cursors.add(cursor)
            query["start_cursor"] = cursor

    def append(self, text: str) -> None:
        text = text.strip()
        if text:
            self.characters += len(text) + (2 if self.parts else 0)
            if self.characters > self.max_characters:
                raise page_limit()
            self.parts.append(text)

    def chapter(self, identifier: UUID) -> Chapter:
        try:
            page = self.get(f"/v1/pages/{identifier}")
            if page["object"] != "page" or UUID(page["id"]) != identifier:
                raise response_error()
            title = next((rich_text(prop["title"]) for prop in page["properties"].values() if prop["type"] == "title"), "")
            self.children(identifier)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise response_error() from exc
        if not self.parts:
            raise ImportFailure("notion_empty", "This Notion page has no readable text. Try a page with text or headings.")
        return text_chapter("\n\n".join(self.parts), title=" ".join(title.split())[:200] or "Untitled Notion page")
