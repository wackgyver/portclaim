"""Windows ViGEm adapter. Never connect the bus during imports/builds/tests."""

# Convert the hub's down-positive evdev/SB10 Y axes to up-positive XInput Y.
INVERT_Y = True


def create_pad():
    import vgamepad as vg
    return vg.VX360Gamepad(), vg
