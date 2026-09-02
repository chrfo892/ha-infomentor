"""Verify the async HA client against the live API, outside Home Assistant."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "infomentor"))

from api import InfoMentorClient, ModuleUnavailable  # noqa: E402


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    username = os.environ["INFOMENTOR_USERNAME"]
    password = os.environ["INFOMENTOR_PASSWORD"]

    async with aiohttp.ClientSession() as session:
        client = InfoMentorClient(session, username, password)
        print("login:", await client.login())

        pupils = await client.async_get_pupils()
        for pupil in pupils:
            print(f"  pupil {pupil.id} = {pupil.name!r}")

        today = date.today()
        monday = today - timedelta(days=today.weekday())

        for pupil in pupils:
            await client.switch_pupil(pupil.id)
            print(f"\n--- {pupil.name} ({pupil.id}) ---")

            for label, coro in (
                ("timetable", client.get_timetable(monday, monday + timedelta(days=13))),
                ("calendar", client.get_calendar(monday - timedelta(days=14),
                                                 monday + timedelta(days=13))),
                ("notifications", client.get_notifications()),
                ("attendance", client.get_attendance()),
            ):
                try:
                    result = await coro
                except ModuleUnavailable:
                    print(f"  {label:<14} n/a for this pupil")
                    continue
                if isinstance(result, list):
                    print(f"  {label:<14} {len(result)} items")
                elif isinstance(result, dict):
                    print(f"  {label:<14} keys={list(result)[:5]}")
                else:
                    print(f"  {label:<14} {type(result).__name__}")

            entries = await client.get_learnlog_entries(page_size=5)
            print(f"  learnlog       {len(entries)} entries")
            for entry in entries[:3]:
                scope = "individual" if entry.is_individual else f"group:{entry.group_name}"
                print(f"      {entry.modified_on} ({scope}) {entry.title[:40]!r} "
                      f"{len(entry.media)} images")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
