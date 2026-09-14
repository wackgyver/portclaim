"""Bounded, wrap-aware UDP freshness tracking (not a network authentication layer)."""
from collections import deque


class StreamGuard:
    def __init__(self, timeout: float = 0.5):
        self.timeout = timeout
        self.seq = None
        self.epoch = None
        self.last_rx = None
        self.retired = deque(maxlen=8)
        self.resync = False
        self.rejected = 0
        self.missing = 0

    def expired(self, now: float) -> bool:
        return self.last_rx is not None and now - self.last_rx >= self.timeout

    def accept(self, seq: int, now: float, epoch: int = 0) -> bool:
        self.resync = False
        if epoch in self.retired:
            self.rejected += 1
            return False
        new_epoch = self.epoch is not None and epoch != self.epoch
        reset = self.seq is None or new_epoch or self.expired(now)
        if not reset:
            step = (seq - self.seq) & 0xFFFFFFFF
            if not 0 < step < 0x80000000:
                self.rejected += 1
                return False
            self.missing += step - 1
        if new_epoch:
            self.retired.append(self.epoch)
        self.resync = reset and self.seq is not None
        self.seq, self.epoch, self.last_rx = seq, epoch, now
        return True
