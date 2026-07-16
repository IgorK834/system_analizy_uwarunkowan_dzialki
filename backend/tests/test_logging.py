import logging
from unittest.mock import patch

from app.core.logging import configure_logging, log_analysis_event
from app.services.context import _finalize_section
from app.services.isok import IsokServiceUnavailableError


def test_configure_logging_sets_required_format_and_keeps_loggers_enabled() -> None:
    with patch("app.core.logging.dictConfig") as mock_config:
        configure_logging()

    config = mock_config.call_args.args[0]
    assert config["disable_existing_loggers"] is False
    log_format = config["formatters"]["default"]["format"]
    assert "%(asctime)s" in log_format
    assert "%(levelname)s" in log_format
    assert "%(name)s" in log_format
    assert "%(message)s" in log_format


def test_analysis_log_contains_operational_fields_and_redacts_unknowns(caplog) -> None:
    analysis_logger = logging.getLogger("app.analysis")
    caplog.set_level(logging.INFO, logger=analysis_logger.name)
    with patch.object(analysis_logger, "disabled", False):
        log_analysis_event(
            "complete",
            parcel_identifier="146101_1.0001.1",
            analysis_id=123,
            status="partial",
            elapsed_ms=45,
            database_url="postgresql://app:sekret@db/dzialki",
            document_bytes=b"%PDF tajna tresc",
            geometry_geojson={"coordinates": [1, 2, 3]},
        )

    assert "analysis_event=complete" in caplog.text
    assert "parcel_identifier=146101_1.0001.1" in caplog.text
    assert "elapsed_ms=45" in caplog.text
    assert "sekret" not in caplog.text
    assert "%PDF" not in caplog.text
    assert "coordinates" not in caplog.text


def test_context_failure_log_does_not_emit_exception_contents(caplog) -> None:
    context_logger = logging.getLogger("app.services.context")
    caplog.set_level(logging.WARNING, logger=context_logger.name)
    with patch.object(context_logger, "disabled", False):
        _finalize_section(
            "isok",
            IsokServiceUnavailableError(
                "postgresql://app:sekret@db/dzialki %PDF prywatna tresc"
            ),
        )

    assert "IsokServiceUnavailableError" in caplog.text
    assert "sekret" not in caplog.text
    assert "%PDF" not in caplog.text
