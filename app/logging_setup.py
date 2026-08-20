import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

_recent_errors: deque[str] = deque(maxlen=40)


class RecentErrorHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            _recent_errors.append(self.format(record))


def recent_errors() -> list[str]:
    return list(_recent_errors)


def configure_logging(level: str = "INFO", max_errors: int = 40) -> None:
    global _recent_errors
    _recent_errors = deque(maxlen=max_errors)
    Path("logs").mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler = RotatingFileHandler("logs/bot.log", maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    recent = RecentErrorHandler()
    recent.setFormatter(formatter)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=[handler, console, recent])
    root = logging.getLogger()
    if recent not in root.handlers:
        root.addHandler(recent)
