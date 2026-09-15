"""Linux virtual gamepad adapter; dependencies load only when a sink starts."""


def create_pad():
    import vgamepad as vg
    return vg.VX360Gamepad(), vg
