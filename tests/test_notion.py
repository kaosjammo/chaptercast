"""Notion link, page extraction and single-request queue regression checks."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.client import HTTPConnection
from io import BytesIO
import json
import socket
from time import monotonic
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from chaptercast import notion
from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.imports import ImportFailure
from chaptercast.jobs import GenerationSettings, JobFailure


PAGE = UUID("5469a2100fcb4be1926cc2e9b7de58d0")
TOGGLE = UUID("5469a2100fcb4be1926cc2e9b7de58d1")
OTHER = UUID("5469a2100fcb4be1926cc2e9b7de58d2")
HEADING = UUID("5469a2100fcb4be1926cc2e9b7de58d3")
LINK = f"https://www.notion.so/Original-test-{PAGE.hex}?pvs=4#block"
TOKEN = "synthetic_read_only_fixture"


def rich(*parts):
    return [{"plain_text": part} for part in parts]


def block(kind, identifier=OTHER, nested=False, **details):
    return {"type": kind, "has_children": nested, "id": str(identifier), kind: details}


def page_response():
    return {"id": str(PAGE), "object": "page",
            "properties": {"Name": {"type": "title", "title": rich("An original test page")}}}


def blocks_response(*blocks, cursor=None):
    return {"object": "list", "results": list(blocks), "has_more": cursor is not None, "next_cursor": cursor}


@pytest.mark.parametrize("link", [
    LINK, f"https://test.notion.site/Synthetic-{PAGE.hex}", f"https://app.notion.com/{PAGE}",
    f"https://www.notion.so/{OTHER.hex}?v={TOGGLE.hex}&p={PAGE.hex}",
])
def test_notion_page_link_forms(link):
    assert notion.page_id(link) == PAGE


@pytest.mark.parametrize("link", [
    f"http://www.notion.so/{PAGE}", f"https://www.notion.so.attacker.test/{PAGE}",
    f"https://fred@www.notion.so/{PAGE}", f"https://www.notion.so:444/{PAGE}",
    f"https://www.notion.so/{PAGE}?p=bad", f"https://www.notion.so/{PAGE}?p={PAGE}&p={OTHER}",
    "https://www.notion.so/Blank", f"https://www.notion.so/\t{PAGE}",
])
def test_unrecognised_links_are_rejected(link):
    with pytest.raises(ImportFailure, match="Notion page link"):
        notion.page_id(link)


def test_page_order_pagination_nested_text_and_no_subpage_requests(monkeypatch):
    paths = []

    def get(_self, path, query=None):
        paths.append((path, dict(query or {})))
        if path == f"/v1/pages/{PAGE}":
            return page_response()
        if path == f"/v1/blocks/{TOGGLE}/children":
            return blocks_response(block("paragraph", rich_text=rich("Nested note.")))
        if path == f"/v1/blocks/{HEADING}/children":
            return blocks_response(block("paragraph", rich_text=rich("Below level four.")))
        if path == f"/v1/blocks/{PAGE}/children" and "start_cursor" not in query:
            return blocks_response(
                block("heading_1", rich_text=rich("Heading")),
                block("paragraph", rich_text=rich("Original ", "prose.")),
                block("toggle", TOGGLE, True, rich_text=rich("Read more")),
                block("heading_4", HEADING, True, rich_text=rich("Level four")),
                block("child_page", OTHER, True, title="Never narrated"),
                cursor="opaque-1",
            )
        if path == f"/v1/blocks/{PAGE}/children" and query["start_cursor"] == "opaque-1":
            return blocks_response(block("table_row", cells=[rich("Left"), rich(""), rich("Right")]))
        raise AssertionError(f"Unexpected request: {path}")

    monkeypatch.setattr(notion.NotionPage, "get", get)
    chapter = notion.NotionPage(TOKEN, 1000).chapter(PAGE)
    assert chapter.title == "An original test page"
    assert chapter.text.split("\n\n") == [
        "Heading", "Original prose.", "Read more", "Nested note.", "Level four",
        "Below level four.", "Left. Right",
    ]
    assert len(paths) == 5
    with pytest.raises(ImportFailure) as failure:
        notion.NotionPage(TOKEN, 20).chapter(PAGE)
    assert failure.value.status == 413


@pytest.mark.parametrize(("statuses", "code"), [
    ((200,), None), ((301,), "notion_unavailable"), ((401,), "notion_invalid_token"),
    ((403,), "notion_page_access"), ((404,), "notion_page_access"), ((429,), "notion_busy"),
    ((429, 200), None), ((429, 429), "notion_busy"),
])
def test_notion_requests_use_only_the_official_api_without_redirects(monkeypatch, statuses, code):
    calls = []
    delays = []
    resolved = []
    endpoint = ("1.1.1.1", 443)

    class Response(BytesIO):
        def __init__(self):
            super().__init__(json.dumps(page_response()).encode())
            self.status = statuses[len(calls) - 1]

        def getheader(self, _):
            return "1" if len(statuses) > 1 else None

    class Transport:
        sock = None

        def __init__(self, host, family, address, timeout, *, deadline):
            assert host == "api.notion.com" and 0 < timeout <= 5
            assert family == socket.AF_INET and address == endpoint and deadline > monotonic()

        def request(self, method, path, *, headers):
            calls.append(path)
            assert method == "GET"
            assert headers["Authorization"] == f"Bearer {TOKEN}"
            assert headers["Notion-Version"] == notion.API_VERSION

        def getresponse(self):
            return Response()

        def close(self):
            pass

    def resolve(host, timeout):
        resolved.append(host)
        assert 0 < timeout <= 3
        return socket.AF_INET, endpoint

    monkeypatch.setattr(notion, "resolve_public", resolve)
    monkeypatch.setattr(notion, "PinnedHTTPSConnection", Transport)
    monkeypatch.setattr(notion, "sleep", delays.append)
    api = notion.NotionPage(TOKEN, 1000)
    path = f"/v1/pages/{PAGE}"
    if code:
        with pytest.raises(ImportFailure) as caught:
            api.get(path)
        assert caught.value.code == code
        assert TOKEN not in caught.value.message
    else:
        assert api.get(path) == page_response()
    assert calls == [path] * len(statuses)
    assert delays == ([1] if len(statuses) > 1 else [])
    assert resolved == ["api.notion.com"]


def test_slow_incomplete_http_headers_are_interrupted_at_the_deadline(monkeypatch):
    client, server = socket.socketpair()
    with client, server:
        client.settimeout(2)
        # The real HTTP parser will block waiting for the rest of these headers.
        server.sendall(b"HTTP/1.1 200 OK\r\nContent-T")

        class SlowConnection(HTTPConnection):
            def __init__(self, host, family, address, timeout, *, deadline):
                super().__init__(host, timeout=timeout)

            def connect(self):
                self.sock = client

        monkeypatch.setattr(notion, "resolve_public", lambda *_: (socket.AF_INET, ("1.1.1.1", 443)))
        monkeypatch.setattr(notion, "PinnedHTTPSConnection", SlowConnection)
        api = notion.NotionPage(TOKEN, 1000)
        began = monotonic()
        api.deadline = began + 0.08
        with pytest.raises(ImportFailure) as caught:
            api.get(f"/v1/pages/{PAGE}")
        assert caught.value.code == "notion_unavailable"
        assert monotonic() - began < 0.6


def test_token_must_be_private_and_regular(tmp_path):
    path = tmp_path / "notion-token"
    for mode in (None, 0o644, 0o600):
        if mode is not None:
            path.write_text(f"{TOKEN}\n")
            path.chmod(mode)
        if mode == 0o600:
            assert notion.read_token(path) == TOKEN
        else:
            with pytest.raises(ImportFailure) as caught:
                notion.read_token(path)
            assert caught.value.code == "notion_not_configured"
    link = tmp_path / "symlink"
    link.symlink_to(path)
    with pytest.raises(ImportFailure):
        notion.read_token(link)


def test_one_request_saves_and_queues_without_duplicates_or_partial_imports(tmp_path, monkeypatch):
    # Keep the queue idle so admission and rollback can be inspected directly.
    monkeypatch.setattr("chaptercast.worker.Worker.start", lambda self: True)
    monkeypatch.setattr("chaptercast.worker.Worker.stop", lambda self: None)
    chapter = notion.Chapter("An original synthetic narration fixture.", "From Notion")

    def fetch(api, page):
        assert api.token == TOKEN and page == PAGE
        return chapter

    monkeypatch.setattr(notion.NotionPage, "chapter", fetch)
    app = create_app(Settings(data_dir=tmp_path), backend=object())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        path = "/api/import/notion"
        assert client.post(path, json={"url": LINK}).json()["code"] == "notion_not_configured"
        token = tmp_path / "notion-token"
        token.write_text(TOKEN)
        token.chmod(0o600)
        assert client.post(path, json={"url": LINK, "speed": 4}).status_code == 400
        first = client.post(path, json={"url": LINK, "voice": "bf_isabella"})
        assert first.status_code == 202
        result = first.json()
        assert result["chapter"]["title"] == chapter.title
        assert result["job"]["status"] == "queued"
        assert result["job"]["voice"] == "bf_isabella"
        assert chapter.text not in first.text and TOKEN not in first.text
        assert client.post(path, json={"url": f"https://www.notion.so/{PAGE}", "voice": "bf_isabella"}).json() == result
        assert len(list((tmp_path / "uploads").iterdir())) == 1

        store = app.state.jobs
        changed = replace(chapter, text="Updated original fixture text.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: store.enqueue_notion(
                changed, f"https://www.notion.so/{PAGE.hex}", GenerationSettings()), range(4)))
        assert all(retry == results[0] for retry in results)
        assert results[0]["chapter"]["id"] != result["chapter"]["id"]

        store.limits = replace(store.limits, max_active=2)
        with pytest.raises(JobFailure, match="active job quota"):
            store.enqueue_notion(replace(chapter, text="Another revision."), LINK, GenerationSettings())
        assert client.get("/api/library").json()["total"] == 2
        assert len(list((tmp_path / "uploads").iterdir())) == 2
