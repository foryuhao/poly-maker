"""A warm library logger must honor CLI stderr/queue reconfiguration."""

import io
import logging

import structlog

from polymaker.logging import configure, get_logger


def test_warm_logger_does_not_keep_printing_to_old_stdout(capsys):
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    old_config = structlog.get_config().copy()
    old_stdout = io.StringIO()
    try:
        structlog.configure(
            logger_factory=structlog.PrintLoggerFactory(old_stdout),
            processors=[structlog.processors.JSONRenderer()],
            wrapper_class=structlog.BoundLogger,
            cache_logger_on_first_use=True,
        )
        logger = get_logger("warm-library")
        logger.info("before_cli")
        configure(console=True)
        logger.info("after_cli")
        assert "before_cli" in old_stdout.getvalue()
        assert "after_cli" not in old_stdout.getvalue()
        captured = capsys.readouterr()
        assert captured.out == "" and "after_cli" in captured.err
    finally:
        root.handlers[:], root.level = old_handlers, old_level
        structlog.configure(**old_config)
