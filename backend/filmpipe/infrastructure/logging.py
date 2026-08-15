from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOG_FORMAT = (
    "%(asctime)s %(levelname)s "
    "job_id=%(job_id)s image_id=%(image_id)s processor=%(processor)s "
    "%(message)s"
)


class FilmPipeLoggerAdapter(logging.LoggerAdapter[Any]):
    def process(
        self,
        msg: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        extra = {
            "job_id": "-",
            "image_id": "-",
            "processor": "-",
        }
        extra.update(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


class ContextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        for key in ("job_id", "image_id", "processor"):
            if not hasattr(record, key):
                setattr(record, key, "-")
        return super().format(record)


def setup_logging(
    log_dir: Path | str = Path("logs"),
    *,
    level: int = logging.INFO,
) -> Path:
    log_path = Path(log_dir) / "filmpipe.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("filmpipe")
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()

    formatter = ContextFormatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)
    logger.addHandler(stream_handler)

    return log_path


def get_logger(
    *,
    job_id: str | None = None,
    image_id: str | None = None,
    processor: str | None = None,
) -> FilmPipeLoggerAdapter:
    logger = logging.getLogger("filmpipe")
    if not logger.handlers:
        setup_logging()

    return FilmPipeLoggerAdapter(
        logger,
        {
            "job_id": job_id or "-",
            "image_id": image_id or "-",
            "processor": processor or "-",
        },
    )
