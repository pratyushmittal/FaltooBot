import logging
from pathlib import Path

FALTOOCHAT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s [session_id=%(session_id)s]: %(message)s"
)


class _SessionFormatter(logging.Formatter):
    def __init__(self, fmt: str, *, session_id: str) -> None:
        super().__init__(fmt)
        self.session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        record.session_id = self.session_id
        return super().format(record)


class _LazyFileHandler(logging.FileHandler):
    _faltoochat_handler = True

    def emit(self, record: logging.LogRecord) -> None:
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        super().emit(record)


def configure_logging(log_path: Path, *, session_id: str) -> None:
    logger = logging.getLogger("faltoobot")
    for handler in list(logger.handlers):
        if getattr(handler, "_faltoochat_handler", False):
            logger.removeHandler(handler)
            handler.close()

    handler = _LazyFileHandler(log_path, encoding="utf-8", delay=True)
    handler.setFormatter(
        _SessionFormatter(FALTOOCHAT_LOG_FORMAT, session_id=session_id)
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
