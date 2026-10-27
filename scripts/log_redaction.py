"""Redact configured credentials and email addresses before log handlers see them."""
import logging
import os
import re
import traceback


def redact(value):
    text = str(value or "")
    for name, secret in sorted(os.environ.items(), key=lambda item: len(item[1]), reverse=True):
        if len(secret) >= 4 and any(part in name.upper() for part in (
            "SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY", "API_KEY", "DATABASE_URL", "INVITE_CODE",
        )):
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)postgres(?:ql)?://[^\s]+", "[REDACTED_DATABASE_URL]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b", "[REDACTED_TOKEN]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    text = re.sub(r"(?im)^DETAIL:.*$", "DETAIL: [REDACTED]", text)
    text = re.sub(r"(?i)(https?://[^\s?#]+)[?#][^\s]+", r"\1?[REDACTED]", text)
    return text


def install_log_redaction():
    previous = logging.getLogRecordFactory()
    if getattr(previous, "totish_redaction", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return record

    factory.totish_redaction = True
    logging.setLogRecordFactory(factory)
