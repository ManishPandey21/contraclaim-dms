"""
Utility helpers for configuring document pipeline logging.

These helpers attach a console handler to specific loggers so that
INFO-level messages are emitted even when the application has not
configured logging globally.
"""

from __future__ import annotations

import logging
from typing import Optional

_PIPELINE_HANDLER_FLAG = "_document_pipeline_handler"


def configure_pipeline_logger(logger: logging.Logger, level: int = logging.INFO, formatter: Optional[logging.Formatter] = None) -> logging.Logger:
    """
    Ensure the given logger has a console handler so pipeline logs are emitted.

    Args:
        logger: Logger instance to configure.
        level: Logging level to apply to both the handler and the logger.
        formatter: Optional formatter for the handler. If omitted, a default formatter is used.

    Returns:
        The configured logger for convenience.
    """
    for handler in logger.handlers:
        if getattr(handler, _PIPELINE_HANDLER_FLAG, False):
            break
    else:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(
            formatter
            or logging.Formatter(
                "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
            )
        )
        setattr(console_handler, _PIPELINE_HANDLER_FLAG, True)
        logger.addHandler(console_handler)

    logger.setLevel(min(level, logger.level) if logger.level else level)
    logger.propagate = False
    return logger


__all__ = ["configure_pipeline_logger"]
