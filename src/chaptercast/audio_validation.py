"""Validate private artifacts independently of the synthesis/encoding adapter."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import wave

from chaptercast.jobs import file_digest


@dataclass(frozen=True)
class Artifact:
    byte_size: int
    duration_seconds: float
    sha256: str


def validate_chunk(path: Path, max_bytes: int) -> Artifact:
    size = path.stat().st_size
    if path.is_symlink() or not 0 < size <= max_bytes:
        raise ValueError("invalid chunk size")
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or not 8000 <= audio.getframerate() <= 48000:
            raise ValueError("chunk must contain mono PCM16 audio")
        frames = audio.getnframes()
        duration = frames / audio.getframerate()
        if not 0 < duration <= 300 or len(audio.readframes(frames)) != frames * 2:
            raise ValueError("invalid or truncated chunk")
    return Artifact(size, duration, file_digest(path))


def validate_output(path: Path, format: str, max_bytes: int, expected_seconds: float) -> Artifact:
    size = path.stat().st_size
    if path.is_symlink() or not 0 < size <= max_bytes:
        raise ValueError("invalid output size")
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_name,codec_type,channels:format=duration,format_name", "-of", "json", str(path),
    ], capture_output=True, timeout=30, check=True)
    data = json.loads(probe.stdout)
    streams = data["streams"]
    container = data["format"]["format_name"].split(",")
    if len(streams) != 1 or streams[0].get("codec_type") != "audio" or streams[0].get("channels") != 1:
        raise ValueError("output must contain one mono audio stream")
    if streams[0]["codec_name"] != {"opus": "opus", "m4a": "aac"}[format] or {"opus": "ogg", "m4a": "mp4"}[format] not in container:
        raise ValueError("incorrect output codec or container")
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or not 0 < duration <= 3600 or abs(duration - expected_seconds) > 0.1:
        raise ValueError("output duration does not cover the completed chunks")
    # Probe metadata alone cannot establish that the complete audio decodes.
    subprocess.run([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-threads", "1", "-i", str(path),
        "-map", "0:a:0", "-f", "null", "-",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)
    return Artifact(size, duration, file_digest(path))
