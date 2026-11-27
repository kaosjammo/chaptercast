"""Bounded DNS and pinned TLS for allowed public HTTPS endpoints."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
import http.client
from ipaddress import ip_address
import socket
import ssl
import threading
import time

# At most two outstanding libc DNS calls, even if a resolver hangs after timeout.
_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chaptercast-dns")
_DNS_SLOTS = threading.BoundedSemaphore(2)


def resolve_public(host: str, timeout: float) -> tuple[int, tuple]:
    if not _DNS_SLOTS.acquire(blocking=False):
        raise OSError("Public DNS resolution is busy.")
    future = _DNS_POOL.submit(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    try:
        records = future.result(timeout=timeout)
    except (FutureTimeout, OSError) as exc:
        raise OSError("Public DNS resolution failed or timed out.") from exc
    if not records:
        raise ValueError("URL has no public address.")
    for family, _, _, _, address in records:
        ip = ip_address(address[0])
        # Reject transition/mapped IPv6 addresses as well as all non-global answers.
        if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None) or getattr(ip, "teredo", None):
            raise ValueError("URL must resolve exclusively to public addresses.")
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("URL has an unsupported address family.")
    return records[0][0], records[0][4]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated numeric address, retain hostname for TLS and Host."""

    def __init__(self, host, family, address, timeout, *, deadline=None):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.family, self.address = family, address
        self.deadline = deadline

    def operation_timeout(self):
        if self.deadline is None:
            return self.timeout
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTPS deadline expired.")
        return min(self.timeout, remaining)

    def connect(self):
        raw = socket.socket(self.family, socket.SOCK_STREAM)
        try:
            self.sock = raw
            self._live_socket = raw
            raw.settimeout(self.operation_timeout())
            raw.connect(self.address)  # No second DNS lookup / proxy environment.
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self._live_socket = self.sock
            self.sock.settimeout(self.operation_timeout())
            self.sock.do_handshake()
        except BaseException:
            self.sock.close()
            raise


@contextmanager
def interrupt_at(connection, deadline):
    def interrupt():
        live_socket = getattr(connection, "_live_socket", None) or connection.sock
        if live_socket:
            try:
                live_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(max(0, deadline - time.monotonic()), interrupt)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
