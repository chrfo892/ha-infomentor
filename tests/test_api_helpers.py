from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

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
        _retry_empty=True,
    )


def _mock_response(text: str) -> MagicMock:
    response = MagicMock()
    response.url = HUB
    response.text = AsyncMock(return_value=text)
    response.read = AsyncMock(return_value=b"")
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    return context


@pytest.mark.parametrize("empty_text", ["", "null", "<html>Login</html>"])
@pytest.mark.parametrize("endpoint", ["get_time_registrations", "get_time_registration_day"])
def test_registration_read_restores_session_and_pupil(empty_text: str, endpoint: str) -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.return_value = _mock_response("")
    expected = (
        {"days": [{"date": "2026-10-22"}]}
        if endpoint == "get_time_registrations"
        else {"date": "2026-10-22", "userComment": "test"}
    )
    session.post.side_effect = [
        _mock_response(empty_text),
        _mock_response(
            '{"days": [{"date": "2026-10-22"}]}'
            if endpoint == "get_time_registrations"
            else '{"date": "2026-10-22", "userComment": "test"}'
        ),
    ]
    client = InfoMentorClient(session, "test", "test")
    client.login = AsyncMock(return_value=True)
    operations = MagicMock()
    operations.attach_mock(client.login, "login")
    operations.attach_mock(session.get, "select_pupil")
    operations.attach_mock(session.post, "read")

    async def run() -> None:
        await client.switch_pupil("test-pupil")
        session.get.reset_mock()
        operations.reset_mock()
        result = await getattr(client, endpoint)(date(2026, 10, 22))
        assert result == expected

    asyncio.run(run())

    client.login.assert_awaited_once()
    session.get.assert_called_once()
    assert session.get.call_args.args[0] == f"{HUB}/Account/PupilSwitcher/SwitchPupil/test-pupil"
    assert session.post.call_count == 2
    assert [operation[0] for operation in operations.method_calls] == [
        "read", "login", "select_pupil", "read"
    ]


def test_registration_read_retries_empty_response_only_once() -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.post.return_value = _mock_response("null")
    client = InfoMentorClient(session, "test", "test")
    client.login = AsyncMock(return_value=True)

    result = asyncio.run(client.get_time_registrations(date(2026, 10, 22)))

    assert result is None
    client.login.assert_awaited_once()
    assert session.post.call_count == 2


@pytest.mark.parametrize("empty_text", ["", "null"])
@pytest.mark.parametrize("endpoint", ["save_time_registration_comment", "save_time_registrations"])
def test_empty_write_response_does_not_retry(empty_text: str, endpoint: str) -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.post.return_value = _mock_response(empty_text)
    client = InfoMentorClient(session, "test", "test")
    client.login = AsyncMock(return_value=True)

    request = (
        client.save_time_registration_comment(123, 0, "test")
        if endpoint == "save_time_registration_comment"
        else client.save_time_registrations([{"timeRegistrationId": 123}])
    )
    result = asyncio.run(request)

    assert result is None
    client.login.assert_not_awaited()
    session.post.assert_called_once()
