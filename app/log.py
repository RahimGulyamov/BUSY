from __future__ import annotations
import typing
import logging
from json_log_formatter import JSONFormatter, VerboseJSONFormatter


def setup_logging() -> None:
    formatter: JSONFormatter = VerboseJSONFormatter()

    json_handler: logging.StreamHandler = logging.StreamHandler()
    json_handler.setFormatter(formatter)

    # Configure root logger
    logger: logging.Logger = logging.getLogger()
    logger.addHandler(json_handler)

    # Overriden in the config, but has to be here
    # to work until the config is loaded
    logger.setLevel(logging.DEBUG)


__all__ = [
    "setup_logging",
]
