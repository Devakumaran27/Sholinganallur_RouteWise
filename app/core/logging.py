import logging
import sys
from app.core.config import settings


def setup_logging() -> None:
    """
    Configures application-wide logging with a standardized format.
    """
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )

    # Align uvicorn loggers with application format
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        log = logging.getLogger(logger_name)
        log.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """
    Factory to retrieve a configured logger by name.
    """
    return logging.getLogger(name)

