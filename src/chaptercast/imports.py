"""Bounded local text/EPUB parsing. Never extract ZIP members to disk."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import re
import stat
from urllib.parse import unquote, urlsplit
import zipfile
import zlib

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_TEXT_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 512
NOTE_PREFIX = re.compile(r"^(?:a\s*/\s*n|author(?:'s|’s|s)?\s+notes?)\s*[:：]", re.I)


class ImportFailure(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Chapter:
    text: str
    title: str = "Imported chapter"

    @property
    def paragraphs(self) -> int:
        return len(re.split(r"\n\s*\n", self.text))


def text_chapter(text: str, exclude_notes: bool = False, title: str = "Imported chapter") -> Chapter:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise ImportFailure("invalid_text", "Text contains invalid Unicode.") from exc
    if size > MAX_TEXT_BYTES:
        raise ImportFailure("too_large", "Chapter text exceeds the 1 MiB limit.", 413)
    if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
        raise ImportFailure("invalid_text", "Text contains unsupported control characters.")
    if re.search(r"<(?:!doctype\s+html|html|body)\b", text, re.I):
        raise ImportFailure("invalid_text", "Paste story text or upload TXT, not an HTML page or bot check.")
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = re.split(r"\n[ \t]*\n(?:[ \t]*\n)*", text)
    if exclude_notes:
        paragraphs = [p for p in paragraphs if not NOTE_PREFIX.match(p.lstrip())]
    text = "\n\n".join(p.strip() for p in paragraphs if p.strip())
    if not text:
        raise ImportFailure("empty_text", "No story text remains to import.")
    return Chapter(text, title)


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFailure("invalid_utf8", "TXT uploads must be UTF-8 encoded.") from exc


def html_paragraphs(root: Tag, exclude_notes: bool) -> str:
    """Keep block boundaries and explicit line breaks; discard inactive/navigation content."""
    for node in list(root.find_all(["script", "style", "nav", "header", "footer", "iframe", "object", "template", "noscript", "svg", "form"])):
        node.decompose()
    for node in list(root.find_all(True)):
        if node.attrs is None:
            continue
        kinds = str(node.get("epub:type", "")).split()
        if node.has_attr("hidden") or node.get("aria-hidden") == "true" or (exclude_notes and set(kinds) & {"footnote", "endnote", "rearnote", "annotation"}):
            node.decompose()
    blocks = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "hr", "aside"}
    parts = []

    stack = [(root, False)]
    while stack:
        node, ending = stack.pop()
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            parts.append(re.sub(r"\s+", " ", str(node)))
        elif isinstance(node, Tag):
            if node.name == "br":
                parts.append("\n")
                continue
            block = node.name in blocks
            if block:
                parts.append("\n\n")
            if ending:
                continue
            if block:
                stack.append((node, True))
            stack.extend((child, False) for child in reversed(list(node.children)))

    text = "".join(parts)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text_chapter(text, exclude_notes).text


def safe_member(name: str) -> str:
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or any(p in {".", "..", ""} for p in name.rstrip("/").split("/")):
        raise ImportFailure("malformed_epub", "EPUB contains an unsafe member path.")
    return name


def xml(data: bytes):
    try:
        return ElementTree.fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except (ElementTree.ParseError, DefusedXmlException, ValueError) as exc:
        raise ImportFailure("malformed_epub", "EPUB metadata is malformed or contains unsafe XML.") from exc


@dataclass(frozen=True)
class EpubChapter:
    id: str
    title: str
    html: str


def read_epub(data: bytes) -> list[EpubChapter]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImportFailure("too_large", "EPUB exceeds the 10 MiB upload limit.", 413)
    try:
        with zipfile.ZipFile(BytesIO(data)) as book:
            members = book.infolist()
            if len(members) > MAX_MEMBERS:
                raise ImportFailure("malformed_epub", "EPUB contains too many archive members.")
            seen = set()
            expanded = 0
            for member in members:
                safe_member(member.filename)
                mode = member.external_attr >> 16
                if member.filename in seen or member.flag_bits & 1 or stat.S_ISLNK(mode) or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ImportFailure("malformed_epub", "EPUB contains duplicate, encrypted or unsupported members.")
                seen.add(member.filename)
                expanded += member.file_size
                if member.file_size > MAX_UPLOAD_BYTES or expanded > MAX_EXPANDED_BYTES or member.file_size > max(1, member.compress_size) * 200:
                    raise ImportFailure("malformed_epub", "EPUB expanded size or compression ratio exceeds the limit.")
                # Read to verify CRC and actual bounded decompression, including unselected members.
                with book.open(member) as stream:
                    if len(stream.read(MAX_UPLOAD_BYTES + 1)) != member.file_size:
                        raise ImportFailure("malformed_epub", "EPUB archive member size is invalid.")
            if book.read("mimetype") != b"application/epub+zip":
                raise ImportFailure("malformed_epub", "Upload is not an EPUB archive.")

            def read_metadata(path):
                if book.getinfo(path).file_size > MAX_HTML_BYTES:
                    raise ImportFailure("malformed_epub", "EPUB metadata or chapter is too large.")
                return book.read(path)

            container = xml(read_metadata("META-INF/container.xml"))
            rootfiles = container.findall("{*}rootfiles/{*}rootfile")
            package_path = next((r.get("full-path") for r in rootfiles if r.get("media-type") == "application/oebps-package+xml"), None)
            if not package_path:
                raise ImportFailure("malformed_epub", "EPUB has no supported package document.")
            safe_member(package_path)
            package = xml(read_metadata(package_path))
            manifest = {}
            for item in package.findall("{*}manifest/{*}item"):
                identifier = item.get("id")
                if not identifier or identifier in manifest:
                    raise ImportFailure("malformed_epub", "EPUB manifest identifiers are missing or duplicated.")
                manifest[identifier] = item
            chapters = []
            for ref in package.findall("{*}spine/{*}itemref"):
                identifier = ref.get("idref")
                item = manifest.get(identifier)
                if item is None:
                    raise ImportFailure("malformed_epub", "EPUB spine references a missing manifest item.")
                if ref.get("linear") == "no" or "nav" in item.get("properties", "").split():
                    continue
                if item.get("media-type") != "application/xhtml+xml":
                    raise ImportFailure("malformed_epub", "EPUB spine contains an unsupported chapter format.")
                href = item.get("href", "")
                target = urlsplit(href)
                if target.scheme or target.netloc or target.query or target.fragment:
                    raise ImportFailure("malformed_epub", "EPUB chapter references must be local archive paths.")
                safe_member(unquote(target.path))
                path = str(PurePosixPath(package_path).parent / unquote(target.path))
                safe_member(path)
                raw = read_metadata(path)
                # EPUB XHTML must be well-formed; reject XML entities before HTML parsing.
                xml(raw)
                if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                    html = raw.decode("utf-16")
                else:
                    html = decode_text(raw)
                soup = BeautifulSoup(html, "html.parser")
                if soup.body is None:
                    raise ImportFailure("malformed_epub", "EPUB chapter has no body.")
                heading = soup.find(["h1", "h2"]) or soup.title
                title = heading.get_text(" ", strip=True)[:200] if heading else f"Chapter {len(chapters) + 1}"
                if identifier in {chapter.id for chapter in chapters}:
                    raise ImportFailure("malformed_epub", "EPUB spine contains repeated chapter identifiers.")
                chapters.append(EpubChapter(identifier, title or "Untitled chapter", html))
            if not chapters:
                raise ImportFailure("malformed_epub", "EPUB has no selectable XHTML chapters.")
            return chapters
    except ImportFailure:
        raise
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError, ValueError, UnicodeError, EOFError, zlib.error, RecursionError) as exc:
        raise ImportFailure("malformed_epub", "EPUB archive is malformed or incomplete.") from exc


def epub_chapter(chapter: EpubChapter, exclude_notes: bool = False) -> Chapter:
    soup = BeautifulSoup(chapter.html, "html.parser")
    return Chapter(html_paragraphs(soup.body, exclude_notes), chapter.title)
