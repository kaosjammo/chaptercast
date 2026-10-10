"""Conservative English sentence boundaries, with lossless bounded fallback."""

import re

BOUNDARY = re.compile(r'[.!?]+[\"\u201d\u2019\')\]]*(?:\s+|$)|\n\s*\n')
ABBREVIATION = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)|\b[A-Z])\.$", re.I)


def split_chunks(text: str, limit: int = 1000) -> list[str]:
    """Keep whole sentences where possible and preserve every input character.

    Long sentences split at whitespace, then at a character boundary for long
    tokens. This is a heuristic, not a linguistic parser; abbreviations without
    whitespace and decimal numbers remain intact.
    """
    if limit < 1:
        raise ValueError("chunk limit must be positive")
    pieces, start = [], 0
    for match in BOUNDARY.finditer(text):
        end = match.end()
        if ABBREVIATION.search(text[start:match.start() + 1]):
            continue
        pieces.append(text[start:end])
        start = end
    if start < len(text):
        pieces.append(text[start:])
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = ""
        while len(piece) > limit:
            split = max(piece.rfind(" ", 0, limit), piece.rfind("\n", 0, limit)) + 1
            split = split or limit
            chunks.append(piece[:split])
            piece = piece[split:]
        current += piece
    if current:
        chunks.append(current)
    return chunks
