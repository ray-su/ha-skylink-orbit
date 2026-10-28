#!/usr/bin/env python3
"""Read-only account check: login, snapshot, observation; no motion interface."""

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

import aiohttp

# Load the bundled standalone client without importing Home Assistant.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "skylink_orbit")
)
from orbitclient import OrbitClient  # noqa: E402
from orbitclient.errors import OrbitError  # noqa: E402


async def check(username: str, password: str, observe: int) -> int:
    async with aiohttp.ClientSession() as session:
        client = OrbitClient(session, username, password)
        try:
            await client.start()
            print(json.dumps(client.diagnostics(), indent=2))
            if observe:
                unsubscribe = client.subscribe(
                    lambda: print(json.dumps(client.diagnostics()))
                )
                try:
                    await asyncio.sleep(observe)
                finally:
                    unsubscribe()
            return 0
        except OrbitError as err:
            print(f"{type(err).__name__}: {err}", file=sys.stderr)
            return 1
        finally:
            await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observe",
        type=int,
        default=30,
        metavar="SECONDS",
        help="observe state updates for 0–3600 seconds (default 30)",
    )
    args = parser.parse_args()
    if not 0 <= args.observe <= 3600:
        parser.error("--observe must be between 0 and 3600")
    print("This signs into Orbit and reads state. It never sends a garage activation.")
    print(
        "Login uses the app's empty push token default; effects on phone notifications are unverified."
    )
    username = input("Orbit email: ").strip()
    password = getpass.getpass("Orbit password (hidden): ")
    if not username or not password:
        parser.error("email and password are required")
    return asyncio.run(check(username, password, args.observe))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
