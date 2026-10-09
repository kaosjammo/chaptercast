"""Start a foreground development server on loopback."""

import uvicorn

from chaptercast.app import create_app
from chaptercast.config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
