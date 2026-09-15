"""Windows ViGEm adapter. Never connect the bus during imports/builds/tests."""


def create_pad():
    import vgamepad as vg
    return vg.VX360Gamepad(), vg
