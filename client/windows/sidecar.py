"""Windows-only SidestickBridge process control; never imported by Linux."""
import os
import subprocess
from client.common.claims import _port_open

def _sidestick_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq SidestickBridge.exe", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return "SidestickBridge.exe" in out


def _start_sidestick() -> None:
    exe = os.environ.get("USB_LOOM_SIDESTICK", "").strip()
    if _port_open(27182):
        print("SidestickBridge already listening on :27182")
        return
    if _sidestick_running():
        print("SidestickBridge is open but Stopped. Click Start bridge — Host IP is this Receiver (Dest / USB_LOOM_SELF).")
        return
    if not os.path.isfile(exe):
        print(f"SidestickBridge not found: {exe}")
        print("Install the receiver or start Remote host yourself, then claim ta320.")
        return
    print(f"starting SidestickBridge --listen  {exe}")
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe, "--listen"], creationflags=creation)


def open_mapping():
    _start_sidestick()
    exe = os.environ.get("USB_LOOM_SIDESTICK", "").strip()
    if os.path.isfile(exe):
        subprocess.Popen([exe], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return "SidestickBridge: click Mapping for the Xbox pad map."
