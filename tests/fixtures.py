"""Pure fixtures: no OS APIs or live streams."""
from proto import tp_native as wire

def descriptor_data():
    geometry = {53: (-3678, 3934, 47), 54: (-2478, 2587, 44),
                58: (0, 253, 0), 48: (0, 1020, 0),
                49: (0, 1020, 0), 52: (-3, 4, 0)}
    return {"schema": 1, "transport": "TP10/N1", "vendor": 0x05AC, "product": 0x0265,
            "bustype": 3, "version": 1, "max_contacts": 5,
            "axes": {str(k): dict(zip(("minimum", "maximum", "resolution"), v)) for k, v in geometry.items()}}

def contact(tid=1, x=0, y=0, slot=0):
    return wire.Contact(slot, tid, x, y, 60, 80, 60, 0)

def frame(*contacts, seq=1, buttons=0, epoch=123):
    return wire.Frame(seq, buttons, tuple(contacts), epoch, 1000000)

class FakeInput:
    def __init__(self, caps, **kwargs):
        self.caps, self.options = caps, kwargs
        self.events = []
        self.closed = False
    def write(self, *event):
        self.events.append(event)
    def syn(self):
        self.events.append((0, 0, 0))
    def close(self):
        self.closed = True
