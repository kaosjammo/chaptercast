"""Transport unit tests with original HTML; no live FFN success is asserted."""
from io import BytesIO
import socket
import ssl

import pytest

from chaptercast import ffn
from chaptercast.imports import ImportFailure

URL = "https://www.fanfiction.net/s/1/1/Original-Fixture"
HTML = b'<html><title>Original fixture</title><div id="storytext"><p>An original paper moon.</p><p>A wooden star.</p></div></html>'


class Response:
    def __init__(self, status=200, headers=None, body=HTML):
        self.status = status
        self.headers = {"Content-Type": "text/html", **(headers or {})}
        self.stream = BytesIO(body)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        return self.stream.read(size)


@pytest.fixture
def transport(monkeypatch):
    calls, replies, resolved = [], [], []
    def resolve(host, timeout):
        resolved.append(host)
        return socket.AF_INET, ("8.8.8.8", 443)
    monkeypatch.setattr(ffn, "resolve_public", resolve)
    class Connection:
        sock = None
        def __init__(self, host, family, address, timeout):
            self.host, self.address = host, address
            self.closed = False
            calls.append(self)
        def request(self, method, path, headers):
            self.method, self.path, self.headers = method, path, headers
        def getresponse(self):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        def close(self):
            self.closed = True
    monkeypatch.setattr(ffn, "PinnedHTTPSConnection", Connection)
    return calls, replies, resolved


def test_synthetic_success_only_story_and_close(transport):
    calls, replies, resolved = transport
    replies.append(Response())
    chapter, source = ffn.fetch_chapter(URL)
    assert chapter.text == "An original paper moon.\n\nA wooden star."
    assert source == URL
    assert resolved == ["www.fanfiction.net"]
    assert calls[0].address == ("8.8.8.8", 443)
    assert calls[0].headers["Accept-Encoding"] == "identity"
    assert calls[0].closed


@pytest.mark.parametrize("status", [403, 429, 503])
def test_blocked_http_response_never_parsed(transport, status):
    calls, replies, _ = transport
    replies.append(Response(status=status))
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == "ffn_blocked"
    assert "Paste text or upload" in exc.value.message
    assert f"HTTP {status}" in exc.value.message
    assert calls[0].closed


@pytest.mark.parametrize("location", ["https://127.0.0.1/private", "http://www.fanfiction.net/s/1/1/Original", "https://example.org/s/1/1/Original", "https://www.fanfiction.net@10.0.0.1/s/1/1/Original", "file:///etc/passwd"])
def test_unsafe_redirect_rejected_before_second_connection(transport, location):
    calls, replies, resolved = transport
    replies.append(Response(302, {"Location": location}))
    with pytest.raises(ImportFailure):
        ffn.fetch_chapter(URL)
    assert len(calls) == 1 and len(resolved) == 1
    assert calls[0].closed


def test_allowed_redirect_revalidated_and_final_url_recorded(transport):
    calls, replies, resolved = transport
    replies.extend([Response(302, {"Location": "https://m.fanfiction.net/s/1/2/Original"}), Response()])
    chapter, url = ffn.fetch_chapter(URL)
    assert resolved == ["www.fanfiction.net", "m.fanfiction.net"]
    assert url == "https://m.fanfiction.net/s/1/2/Original"
    assert all(c.closed for c in calls)


def test_redirect_to_allowed_host_with_private_dns_rejected(monkeypatch, transport):
    calls, replies, _ = transport
    replies.append(Response(302, {"Location": "https://m.fanfiction.net/s/1/2/Original"}))
    def resolve(host, timeout):
        if host == "m.fanfiction.net":
            raise ImportFailure("unsafe_url", "URL must resolve exclusively to public addresses.")
        return socket.AF_INET, ("8.8.8.8", 443)
    monkeypatch.setattr(ffn, "resolve_public", resolve)
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == "unsafe_url" and len(calls) == 1


def test_redirect_loop_is_bounded(transport):
    calls, replies, _ = transport
    replies.extend(Response(302, {"Location": URL}) for _ in range(4))
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == "unsafe_redirect" and len(calls) == 4
    assert all(c.closed for c in calls)


@pytest.mark.parametrize("headers,body,code", [({"Content-Length": "99999999"}, HTML, "too_large"), ({"Content-Length": "bad"}, HTML, "invalid_response"), ({"Content-Length": "2000"}, HTML, "invalid_response"), ({"Content-Encoding": "gzip"}, HTML, "invalid_response"), ({"Content-Type": "application/pdf"}, HTML, "invalid_response"), ({}, b"\xff", "invalid_response"), ({}, b"<html><title>Just a moment</title></html>", "ffn_blocked")])
def test_response_validation(transport, headers, body, code):
    calls, replies, _ = transport
    replies.append(Response(headers=headers, body=body))
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == code and calls[0].closed


def test_streamed_overflow_without_content_length(monkeypatch, transport):
    calls, replies, _ = transport
    monkeypatch.setattr(ffn, "MAX_HTML_BYTES", 20)
    replies.append(Response(body=b"x" * 21))
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == "too_large" and calls[0].closed


def test_timeout_is_explicit_and_connection_closed(transport):
    calls, replies, _ = transport
    replies.append(socket.timeout("original simulated timeout"))
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == "ffn_unavailable" and calls[0].closed


def test_overall_deadline_interrupts_stream_even_with_small_blocks(monkeypatch, transport):
    calls, replies, _ = transport
    clock = [0.0]
    monkeypatch.setattr(ffn.time, "monotonic", lambda: clock[0])
    class SlowResponse(Response):
        def read1(self, size):
            clock[0] += ffn.FETCH_SECONDS + 1
            return b"x"
    replies.append(SlowResponse())
    with pytest.raises(ImportFailure) as exc:
        ffn.fetch_chapter(URL)
    assert exc.value.code == "ffn_unavailable" and calls[0].closed


def test_mixed_public_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(ffn.socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, 1, 6, "", ("8.8.8.8", 443)), (socket.AF_INET, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(ImportFailure) as exc:
        ffn.resolve_public("www.fanfiction.net", 3)
    assert exc.value.code == "unsafe_url"


def test_connection_pins_numeric_address_and_checks_hostname(monkeypatch):
    calls = []
    class RawSocket:
        def settimeout(self, seconds):
            calls.append(("timeout", seconds))
        def connect(self, address):
            calls.append(("connect", address))
        def close(self):
            pass
        def do_handshake(self):
            calls.append(("handshake", True))
    class Context:
        def wrap_socket(self, raw, server_hostname, do_handshake_on_connect):
            assert do_handshake_on_connect is False
            calls.append(("tls_hostname", server_hostname))
            return raw
    connection = ffn.PinnedHTTPSConnection("www.fanfiction.net", socket.AF_INET, ("8.8.8.8", 443), 3)
    assert connection._context.check_hostname
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    connection._context = Context()
    monkeypatch.setattr(ffn.socket, "socket", lambda *a: RawSocket())
    monkeypatch.setattr(ffn.socket, "getaddrinfo", lambda *a: pytest.fail("must not resolve again"))
    connection.connect()
    assert ("connect", ("8.8.8.8", 443)) in calls
    assert ("tls_hostname", "www.fanfiction.net") in calls
    connection.close()
