#!/usr/bin/env python3
"""Own one temporary virtual microphone; never set desktop audio defaults."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import uuid

SINK = "portclaim_mic"


def pactl(*args):
    return subprocess.check_output(["pactl", *args], text=True, timeout=5).strip()


def state_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise OSError("XDG_RUNTIME_DIR is unset; run in the desktop user session")
    return Path(runtime) / "portclaim" / "mic-module.json"


def sinks():
    return json.loads(pactl("--format=json", "list", "sinks"))


def owns_module(state):
    # Module IDs may be reused after a server restart. Require our random token
    # in the original module arguments, not just an ID and a familiar sink name.
    for line in pactl("list", "short", "modules").splitlines():
        row = line.split("\t")
        if len(row) < 3 or row[0] != str(state["module"]) or row[1] != "module-null-sink":
            continue
        try:
            arguments = dict(item.split("=", 1) for item in shlex.split(row[2]) if "=" in item)
            properties = shlex.split(arguments.get("sink_properties", ""))
        except ValueError:
            continue
        if arguments.get("sink_name") == SINK and f"portclaim.owner={state['owner']}" in properties:
            return True
    return False


def save_state(path, state):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".mic-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stop(path):
    if not path.exists():
        return
    state = json.loads(path.read_text())
    if (type(state.get("module")) is not int or state["module"] < 0
            or not re.fullmatch(r"[a-f0-9]{32}", str(state.get("owner", "")))):
        raise ValueError("invalid mic ownership record; no module unloaded")
    if owns_module(state):
        pactl("unload-module", str(state["module"]))
        print("Removed owned PortClaim Mic.")
    else:
        print("Owned mic no longer present; existing audio devices left untouched.")
    path.unlink()


def start(path):
    if path.exists():
        stop(path)
    if any(sink.get("name") == SINK for sink in sinks()):
        print("Using existing PortClaim Mic without taking ownership.")
        return
    owner = uuid.uuid4().hex
    module = pactl("load-module", "module-null-sink", f"sink_name={SINK}",
                   f'sink_properties="device.description=PortClaim_Mic portclaim.owner={owner}"')
    if not module.isdecimal():
        raise ValueError("pactl returned an invalid module ID")
    state = {"module": int(module), "owner": owner}
    try:
        if not owns_module(state):
            raise OSError(f"module {module} ownership could not be verified; not unloading an unknown module")
        save_state(path, state)
    except BaseException:
        # Clean up only if the module is still ours. A cleanup failure is
        # reported; never unload a reused module ID or pretend setup succeeded.
        if owns_module(state):
            pactl("unload-module", module)
        raise
    print("Created temporary PortClaim Mic; desktop defaults unchanged.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    args = parser.parse_args(argv)
    try:
        (start if args.action == "start" else stop)(state_path())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"PortClaim virtual mic {args.action} failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
