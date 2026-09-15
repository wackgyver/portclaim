"""Explicit Linux legacy mouse fallback; native input never imports this module."""
from client.common.input_codes import *
NAME = "uinput (legacy mouse)"
_linux_ui = None
_LINUX_VK = {
    VK_CONTROL: "KEY_LEFTCTRL",
    VK_MENU: "KEY_LEFTALT",
    VK_LEFT: "KEY_LEFT",
    VK_UP: "KEY_UP",
    VK_RIGHT: "KEY_RIGHT",
    VK_DOWN: "KEY_DOWN",
    VK_LWIN: "KEY_LEFTMETA",
    VK_TAB: "KEY_TAB",
    VK_N: "KEY_N",
    VK_D: "KEY_D",
}


def _linux_uinput():
    global _linux_ui
    if _linux_ui is not None:
        return _linux_ui
    from evdev import UInput, ecodes

    keys = [
        ecodes.BTN_LEFT,
        ecodes.BTN_RIGHT,
        ecodes.BTN_MIDDLE,
        ecodes.KEY_LEFTCTRL,
        ecodes.KEY_LEFTALT,
        ecodes.KEY_LEFTMETA,
        ecodes.KEY_LEFT,
        ecodes.KEY_RIGHT,
        ecodes.KEY_UP,
        ecodes.KEY_DOWN,
        ecodes.KEY_TAB,
        ecodes.KEY_N,
        ecodes.KEY_D,
    ]
    _linux_ui = UInput(
        {
            ecodes.EV_KEY: keys,
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL, ecodes.REL_HWHEEL],
        },
        name="PortClaim Trackpad",
    )
    return _linux_ui


def _linux_key(vk: int):
    from evdev import ecodes

    name = _LINUX_VK.get(int(vk))
    if not name:
        return None
    return getattr(ecodes, name)


def mouse_move(dx: int, dy: int) -> None:
    if dx == 0 and dy == 0:
        return
    from evdev import ecodes

    ui = _linux_uinput()
    if dx:
        ui.write(ecodes.EV_REL, ecodes.REL_X, int(dx))
    if dy:
        ui.write(ecodes.EV_REL, ecodes.REL_Y, int(dy))
    ui.syn()
    return


def mouse_btn(flags: int) -> None:
    from evdev import ecodes

    ui = _linux_uinput()
    mapping = (
        (MOUSEEVENTF_LEFTDOWN, ecodes.BTN_LEFT, 1),
        (MOUSEEVENTF_LEFTUP, ecodes.BTN_LEFT, 0),
        (MOUSEEVENTF_RIGHTDOWN, ecodes.BTN_RIGHT, 1),
        (MOUSEEVENTF_RIGHTUP, ecodes.BTN_RIGHT, 0),
    )
    for mask, code, value in mapping:
        if flags & mask:
            ui.write(ecodes.EV_KEY, code, value)
    ui.syn()
    return


def mouse_wheel(vertical: int = 0, horizontal: int = 0) -> None:
    from evdev import ecodes

    ui = _linux_uinput()
    if vertical:
        ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(vertical))
    if horizontal:
        ui.write(ecodes.EV_REL, ecodes.REL_HWHEEL, int(horizontal))
    if vertical or horizontal:
        ui.syn()
    return


def key_ctrl(down: bool) -> None:
    from evdev import ecodes

    code = _linux_key(VK_CONTROL)
    if code is None:
        return
    ui = _linux_uinput()
    ui.write(ecodes.EV_KEY, code, 1 if down else 0)
    ui.syn()
    return


def key_chord(vks: list[int]) -> None:
    if not vks:
        return
    from evdev import ecodes

    ui = _linux_uinput()
    codes = [c for c in (_linux_key(vk) for vk in vks) if c is not None]
    for code in codes:
        ui.write(ecodes.EV_KEY, code, 1)
    for code in reversed(codes):
        ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()
    return



def open():
    _linux_uinput()


def restore_ole_drag_defaults():
    pass


def unpark_ole_drag():
    pass


def close():
    global _linux_ui
    if _linux_ui is not None:
        _linux_ui.close()
        _linux_ui = None
