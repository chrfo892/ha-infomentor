from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "infomentor"))

from api import (  # noqa: E402
    LearnLogEntry,
    MediaFile,
    parse_swedish_datetime,
    plain_text_from_html,
    safe_path_part,
)


def test_parse_swedish_datetime() -> None:
    assert parse_swedish_datetime("den 24 augusti 2026 klockan 21:38") is not None
    value = parse_swedish_datetime("den 24 augusti 2026 klockan 21:38")
    assert value.isoformat() == "2026-08-24T21:38:00"


def test_safe_path_part() -> None:
    assert safe_path_part("Andersson, Anna") == "Anna_Andersson"
    assert safe_path_part("Sjöstjärnan") == "Sjostjarnan"


def test_plain_text_from_html() -> None:
    assert plain_text_from_html("<p>Hej</p><p>Ta med <strong>kläder</strong>.</p>") == (
        "Hej\nTa med kläder."
    )


def test_learnlog_scope() -> None:
    individual = LearnLogEntry(1, "Post", "", None)
    group = LearnLogEntry(2, "Post", "Example group", None)
    assert individual.is_individual
    assert not group.is_individual


def test_media_filename_is_safe_and_date_prefixed() -> None:
    media = MediaFile(
        file_id=1,
        name="IMG 1",
        url="/resource",
        extension="jpeg",
        source="learnlog",
        entry_date="2026-09-01",
    )
    assert media.filename == "2026-09-01_IMG_1.jpeg"
