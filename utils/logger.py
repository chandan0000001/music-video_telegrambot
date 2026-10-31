"""
Logging Configuration
Custom logger with token redaction to prevent accidental credential leakage in logs.
"""
import re
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config import LOGS_DIR, BOT_TOKEN

# Regex matching standard Telegram Bot Tokens: e.g. 123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567
TOKEN_REGEX = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{35}\b")


class RedactingFilter(logging.Filter):
    """Filter that masks Telegram bot tokens in all logged records."""

    def __init__(self, token_to_mask: Optional[str] = None):
        super().__init__()
        self.token_to_mask = token_to_mask

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = TOKEN_REGEX.sub("[REDACTED_BOT_TOKEN]", record.msg)
            if self.token_to_mask and len(self.token_to_mask) > 10 and self.token_to_mask in record.msg:
                record.msg = record.msg.replace(self.token_to_mask, "[REDACTED_BOT_TOKEN]")

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (TOKEN_REGEX.sub("[REDACTED_BOT_TOKEN]", v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    (TOKEN_REGEX.sub("[REDACTED_BOT_TOKEN]", str(arg)) if isinstance(arg, str) else arg)
                    for arg in record.args
                )
        return True


def setup_logger(name: str = "music_bot", log_file: Optional[Path | str] = None) -> logging.Logger:
    """Configure and return root/application logger."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_path = Path(log_file or (LOGS_DIR / "bot.log"))

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # Rotating File Handler (10 MB per file, max 5 backup files)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Attach redaction filter to handlers
    redactor = RedactingFilter(BOT_TOKEN if BOT_TOKEN != "PASTE_NEW_REGENERATED_BOT_TOKEN_HERE" else None)
    console_handler.addFilter(redactor)
    file_handler.addFilter(redactor)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
