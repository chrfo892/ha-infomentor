"""Check whether notifications can be filtered to the selected pupil."""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "infomentor"))

from api import InfoMentorClient  # noqa: E402


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async with aiohttp.ClientSession() as session:
        client = InfoMentorClient(
            session, os.environ["INFOMENTOR_USERNAME"], os.environ["INFOMENTOR_PASSWORD"]
        )
        await client.login()

        for pupil in await client.async_get_pupils():
            await client.switch_pupil(pupil.id)
            data = await client.get_notifications() or {}
            items = data.get("notifications", [])
            selected = [i for i in items if i.get("currentlySelectedPupil")]
            im2 = Counter(i.get("pupilIM2Id") for i in items)
            print(f"\n--- {pupil.name} ({pupil.id}) ---")
            print(f"  total={len(items)}  currentlySelectedPupil=True -> {len(selected)}")
            print(f"  pupilIM2Id counts: {dict(im2)}")
            for item in items[:6]:
                print(f"    sel={str(item.get('currentlySelectedPupil')):<5} "
                      f"im2={item.get('pupilIM2Id')} "
                      f"{(item.get('title') or '')[:40]!r} {item.get('dateSent')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
