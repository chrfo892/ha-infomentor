"""Verify the time registration endpoints for every pupil."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from datetime import date
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "infomentor"))

from api import (  # noqa: E402
    InfoMentorClient,
    ModuleUnavailable,
    time_registration_response_summary,
)


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    username = os.environ.get("INFOMENTOR_USERNAME") or getpass.getpass(
        "InfoMentor username (hidden): "
    )
    password = os.environ.get("INFOMENTOR_PASSWORD") or getpass.getpass(
        "InfoMentor password (hidden): "
    )

    async with aiohttp.ClientSession() as session:
        client = InfoMentorClient(session, username, password)
        await client.login()

        for pupil in await client.async_get_pupils():
            await client.switch_pupil(pupil.id)
            print(f"\n--- {pupil.name} ({pupil.id}) ---")

            try:
                week = await client.get_time_registrations()
                print("  default week:", time_registration_response_summary(week))
                dated_week = await client.get_time_registrations(args.date)
                print("  requested week:", time_registration_response_summary(dated_week))
            except ModuleUnavailable:
                print("  time registration not available for this pupil")
                continue

            days = (week or {}).get("days", [])
            print(f"  week {(week or {}).get('startDate','?')[:10]} .. "
                  f"{(week or {}).get('endDate','?')[:10]}, {len(days)} days")
            for day in days:
                print(f"    {day['date'][:10]} {day.get('startDateTime','')[11:16]}"
                      f"-{day.get('endDateTime','')[11:16]}"
                      f" leave={day.get('onLeave')} closed={day.get('isSchoolClosed')}"
                      f" hasComments={day.get('hasComments')}")

            try:
                today = await client.get_time_registration_day(args.date)
            except ModuleUnavailable:
                print("  comments not available")
                continue
            print("  requested day:", time_registration_response_summary(today))

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
