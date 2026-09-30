#!/usr/bin/env python3
"""Compile an offline Linux-header ABI probe; never open video devices or ioctl."""
from __future__ import annotations
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))
import v4l2_capture as v4l2


def check():
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("cc and Linux userspace headers are required for the offline ABI check")
    structures = {"Capability": "v4l2_capability", "Format": "v4l2_format", "StreamParm": "v4l2_streamparm",
                  "RequestBuffers": "v4l2_requestbuffers", "Buffer": "v4l2_buffer"}
    calls = {"VIDIOC_QUERYCAP": (0, v4l2.Capability, 2), "VIDIOC_S_FMT": (5, v4l2.Format, 3),
             "VIDIOC_REQBUFS": (8, v4l2.RequestBuffers, 3), "VIDIOC_QUERYBUF": (9, v4l2.Buffer, 3),
             "VIDIOC_QBUF": (15, v4l2.Buffer, 3), "VIDIOC_DQBUF": (17, v4l2.Buffer, 3),
             "VIDIOC_STREAMON": (18, ctypes.c_int, 1), "VIDIOC_STREAMOFF": (19, ctypes.c_int, 1),
             "VIDIOC_S_PARM": (22, v4l2.StreamParm, 3)}
    fields = {"Format.fmt": ("v4l2_format", "fmt"), "Buffer.timestamp": ("v4l2_buffer", "timestamp"),
              "Buffer.m": ("v4l2_buffer", "m"), "Buffer.length": ("v4l2_buffer", "length")}
    lines = ['#include <stdio.h>', '#include <stddef.h>', '#include <time.h>',
             '#include <sys/time.h>', '#include <linux/videodev2.h>', 'int main(void) {']
    for name, ctype in structures.items():
        lines.append(f'printf("{name} %zu\\n", sizeof(struct {ctype}));')
    for name in calls:
        lines.append(f'printf("{name} %lu\\n", (unsigned long){name});')
    for name, (ctype, field) in fields.items():
        lines.append(f'printf("{name} %zu\\n", offsetof(struct {ctype}, {field}));')
    lines.append('return 0; }')
    with tempfile.TemporaryDirectory(prefix="portclaim-camera-abi-") as directory:
        source, program = Path(directory) / "check.c", Path(directory) / "check"
        source.write_text("\n".join(lines))
        subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(program)],
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run([str(program)], check=True, capture_output=True, text=True, timeout=5)
    observed = {name: int(value) for name, value in (line.split() for line in result.stdout.splitlines())}
    expected = {name: ctypes.sizeof(getattr(v4l2, name)) for name in structures}
    expected.update({name: v4l2.request(number, kind, direction) for name, (number, kind, direction) in calls.items()})
    expected.update({name: getattr(getattr(v4l2, name.split('.')[0]), name.split('.')[1]).offset for name in fields})
    if observed != expected:
        raise RuntimeError(f"Linux video ABI mismatch: C={observed}, Python={expected}")
    # Receiver's minimal QUERYCAP/S_FMT/S_PARM layouts use these same sizes.
    if observed["Capability"] != 104 or observed["StreamParm"] != 204:
        raise RuntimeError("unsupported receiver video ABI")
    if observed["Format"] != (8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4) + 200:
        raise RuntimeError("unsupported receiver format-union alignment")
    print("Camera V4L2 structs, offsets and ioctl numbers match Linux headers; no devices opened")
    return observed


if __name__ == "__main__":
    check()
