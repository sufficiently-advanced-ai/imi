import logging

from app.core.logging_setup import configure_logging


def test_configure_logging_is_idempotent_and_sets_level(monkeypatch):
    root = logging.getLogger()
    before = [h for h in root.handlers if getattr(h, "_imi_root_handler", False)]
    configure_logging("DEBUG", force=True)
    configure_logging("DEBUG", force=True)
    after = [h for h in root.handlers if getattr(h, "_imi_root_handler", False)]
    assert len(after) == max(1, len(before))
    assert root.level == logging.DEBUG
    assert logging.getLogger("httpx").level >= logging.WARNING
    configure_logging("INFO", force=True)
    assert root.level == logging.INFO


def test_app_logger_info_reaches_root_handler(caplog):
    configure_logging("INFO", force=True)
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.core.lifecycle").info("ready to serve requests")
    assert any("ready to serve requests" in r.message for r in caplog.records)
