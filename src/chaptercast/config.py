"""Small environment-based configuration; no credentials are needed."""

from dataclasses import dataclass, field
from ipaddress import ip_address
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = field(default_factory=lambda: Path(".runtime"))

    def __post_init__(self) -> None:
        try:
            loopback = self.host == "localhost" or ip_address(self.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("host must be a loopback address or localhost")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser().resolve())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        try:
            port = int(values.get("CHAPTERCAST_PORT", "8000"))
        except ValueError as exc:
            raise ValueError("port must be an integer between 1 and 65535") from exc
        return cls(
            host=values.get("CHAPTERCAST_HOST", "127.0.0.1"),
            port=port,
            data_dir=Path(values.get("CHAPTERCAST_DATA_DIR", ".runtime")),
        )
