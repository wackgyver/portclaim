#!/usr/bin/python3
"""Control one PortClaim service; showing/hiding never restarts its streams."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time

UNIT = "portclaim.service"


def window_signal(signum):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = int(subprocess.check_output(
            ["systemctl", "--user", "show", UNIT, "-p", "MainPID", "--value"], text=True).strip() or "0")
        if not pid:
            raise SystemExit("PortClaim is not running.")
        try:
            fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
            caught = int(fields.get("SigCgt", "0").strip(), 16)
            if caught & (1 << (signum - 1)):
                os.kill(pid, signum)
                return
        except ProcessLookupError:
            pass
        except FileNotFoundError:
            pass
        # Never signal an initializing/old receiver with SIGUSR's default action.
        time.sleep(0.1)
    raise SystemExit("Receiver window controls did not become ready; no signal sent.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="start", choices=["start", "show", "hide", "stop", "status"])
    args = parser.parse_args()
    if args.action in {"start", "show"}:
        subprocess.run(["systemctl", "--user", "start", UNIT], check=True)
        window_signal(signal.SIGUSR1)
    elif args.action == "hide":
        window_signal(signal.SIGUSR2)
    else:
        return subprocess.run(["systemctl", "--user", args.action, UNIT]).returncode
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"PortClaim control failed: {exc}") from None
