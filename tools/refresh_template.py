#!/usr/bin/env python3
"""Refresh the test template's Wine prefix after Steam or the pinned Proton changed, so launches stop rewriting it.

    uv run python tools/refresh_template.py

It waits until no game runs and holds both game slots while Proton updates the prefix, because every running game
sees the template prefix through its overlay. It does nothing when prepare_run would already accept the template.
Proton's run never returns on its own here (Wine's services keep the session open), so once prepare_run accepts the
template it ends the session with wineserver -k, which saves the registry.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import machine_lock
from game_harness import SLOTS
from prepare_run import WORK, check_template

TEMPLATE = WORK / "runtime-tests/_template"
STEAM = Path.home() / ".local/share/Steam"
DEADLINE = 600  # seconds; the update took about 30 s on 2026-11-15


def current() -> bool:
    try:
        check_template(TEMPLATE)
        return True
    except ValueError:
        return False


def main() -> int:
    if current():
        print(f"{TEMPLATE} is current; nothing to refresh")
        return 0
    for name in SLOTS:  # one at a time, as a launch takes them, so a waiting launch can say who holds what
        print(f"waiting for {name}: {', '.join(machine_lock.holders((name,))) or 'free'}", flush=True)
        machine_lock.take((name,), "refresh-template")
    env = dict(os.environ, STEAM_COMPAT_DATA_PATH=str(TEMPLATE / "prefix"), STEAM_COMPAT_CLIENT_INSTALL_PATH=str(STEAM),
               STEAM_COMPAT_APP_ID="0", SteamAppId="0", SteamGameId="0", PROTON_USE_XALIA="0")  # as launch.sh runs it
    proton = subprocess.Popen([str(STEAM / "steamapps/common/SteamLinuxRuntime_4/run"), "--batch", "--",
                               str(TEMPLATE / "proton/proton"), "run", "wineboot.exe", "-u"], env=env)
    start = time.monotonic()
    while proton.poll() is None and not current() and time.monotonic() - start < DEADLINE:
        time.sleep(2)
    while proton.poll() is None:  # Wine may start only after config_info is written, so end it until Proton returns
        subprocess.run([str(TEMPLATE / "proton/files/bin/wineserver"), "-k"],
                       env=dict(os.environ, WINEPREFIX=str(TEMPLATE / "prefix/pfx")), check=False)
        try:
            proton.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if time.monotonic() - start > DEADLINE:
                proton.kill()
                raise SystemExit(f"Proton did not finish updating {TEMPLATE / 'prefix'} in {DEADLINE} s") from None
    check_template(TEMPLATE)  # raises with the reason when the update did not finish
    print(f"refreshed {TEMPLATE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
