"""Linux virtual gamepad adapter; dependencies load only when a sink starts."""

# SB10 retains Linux xpad's down-positive Y. vgamepad 0.1.0 writes its Y report
# values directly to ABS_Y/ABS_RY, so Linux must not apply the XInput inversion.
INVERT_Y = False


def create_pad():
    import vgamepad as vg
    return vg.VX360Gamepad(), vg
