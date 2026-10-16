from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "infomentor"))

from api import (  # noqa: E402
    HUB,
    InfoMentorClient,
    LearnLogEntry,
    MediaFile,
    parse_swedish_datetime,
    plain_text_from_html,
    safe_path_part,
    time_registration_response_summary,
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


def test_time_registration_response_summary_distinguishes_empty_responses() -> None:
    assert time_registration_response_summary(None) == "empty/null response"
    assert time_registration_response_summary({}) == "fields=[none]; days=missing"
    assert time_registration_response_summary({"days": []}) == "fields=[days]; days=0 entries"
    assert time_registration_response_summary([]) == "unexpected response type: list"


def test_time_registration_response_summary_excludes_values() -> None:
    summary = time_registration_response_summary(
        {"error": "private error detail", "userComment": "private comment"}
    )
    assert summary == "fields=[error, userComment]; days=missing"


def test_time_registrations_request_keeps_explicit_date() -> None:
    client = InfoMentorClient(AsyncMock(spec=aiohttp.ClientSession), "test", "test")
    client._json = AsyncMock(return_value={"days": []})

    asyncio.run(client.get_time_registrations(date(2026, 10, 15)))

    client._json.assert_awaited_once_with(
        f"{HUB}/TimeRegistration/TimeRegistration/GetTimeRegistrations/",
        json_body={
            "date": "2026-10-15T00:00:00",
            "showNextWeekIfNoMoreSchoolDays": False,
        },
    )
