"""WASAPI shared-mode render — Steam Streaming Microphone wants mix format, not MME."""

from __future__ import annotations

import array
import ctypes
import struct
from ctypes import wintypes

ole32 = ctypes.windll.ole32
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
ole32.CoCreateInstance.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
eRender = 0
DEVICE_STATE_ACTIVE = 1
STGM_READ = 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRCDEFAULTQUALITY = 0x08000000
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
VT_LPWSTR = 31


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> GUID:
    a, b, c, d, e = text.split("-")
    data4 = bytes.fromhex(d + e)
    return GUID(int(a, 16), int(b, 16), int(c, 16), (ctypes.c_ubyte * 8).from_buffer_copy(data4))


CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioRenderClient = _guid("F294ACFC-3146-4483-A7BF-ADDCA7C260E2")
IID_IPropertyStore = _guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
PKEY_NAME_FMTID = _guid("A45C254E-DF1C-4EFD-8020-67D146A850E0")


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("wReserved1", wintypes.USHORT),
        ("wReserved2", wintypes.USHORT),
        ("wReserved3", wintypes.USHORT),
        ("data", ctypes.c_void_p),
    ]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


def _vtbl(punk: ctypes.c_void_p) -> ctypes.POINTER(ctypes.c_void_p):
    return ctypes.cast(punk, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _fn(punk: ctypes.c_void_p, index: int, restype, *argtypes):
    func = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(_vtbl(punk)[index])
    return lambda *args: func(punk, *args)


def _check(hr: int, what: str) -> None:
    hr = ctypes.c_long(hr).value
    if hr < 0:
        raise OSError(f"{what} failed: 0x{hr & 0xFFFFFFFF:08X}")


class WasapiOut:
    def __init__(self, name_needles: tuple[str, ...]):
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        enumerator = ctypes.c_void_p()
        _check(
            ole32.CoCreateInstance(
                ctypes.byref(CLSID_MMDeviceEnumerator),
                None,
                CLSCTX_ALL,
                ctypes.byref(IID_IMMDeviceEnumerator),
                ctypes.byref(enumerator),
            ),
            "CoCreateInstance MMDeviceEnumerator",
        )
        enum_audio = _fn(
            enumerator, 3, ctypes.HRESULT, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)
        )
        collection = ctypes.c_void_p()
        _check(enum_audio(eRender, DEVICE_STATE_ACTIVE, ctypes.byref(collection)), "EnumAudioEndpoints")
        get_count = _fn(collection, 3, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))
        item = _fn(collection, 4, ctypes.HRESULT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        count = wintypes.UINT()
        _check(get_count(ctypes.byref(count)), "GetCount")
        named: list[tuple[ctypes.c_void_p, str]] = []
        for i in range(count.value):
            dev = ctypes.c_void_p()
            if item(i, ctypes.byref(dev)) < 0:
                continue
            named.append((dev, _device_name(dev)))
        device = None
        chosen = ""
        for needle in name_needles:
            for dev, name in named:
                if needle in name.lower():
                    device = dev
                    chosen = name
                    break
            if device is not None:
                break
        if device is None:
            raise OSError(f"no WASAPI render matching {name_needles}")
        self.name = chosen
        activate = _fn(
            device,
            3,
            ctypes.HRESULT,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        client = ctypes.c_void_p()
        _check(
            activate(ctypes.byref(IID_IAudioClient), CLSCTX_ALL, None, ctypes.byref(client)),
            "Activate IAudioClient",
        )
        get_mix = _fn(client, 8, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))
        mix_ptr = ctypes.c_void_p()
        _check(get_mix(ctypes.byref(mix_ptr)), "GetMixFormat")
        fmt = ctypes.cast(mix_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
        self.rate = int(fmt.nSamplesPerSec)
        self.channels = int(fmt.nChannels) or 2
        tag = int(fmt.wFormatTag)
        bits = int(fmt.wBitsPerSample) or 32
        self.floating = tag == WAVE_FORMAT_IEEE_FLOAT
        if tag == WAVE_FORMAT_EXTENSIBLE and fmt.cbSize >= 22:
            extra = ctypes.string_at(mix_ptr.value + ctypes.sizeof(WAVEFORMATEX), 22)
            valid = int.from_bytes(extra[0:2], "little")
            if valid:
                bits = valid
            sub = extra[6:22]
            float_sub = bytes.fromhex("0300000000001000800000aa00389b71")
            pcm_sub = bytes.fromhex("0100000000001000800000aa00389b71")
            self.floating = sub == float_sub or bits == 32
            if sub == pcm_sub:
                self.floating = False
        self.bits = bits or 32
        if self.bits not in (16, 24, 32):
            self.bits = 32
            self.floating = True
        initialize = _fn(
            client,
            3,
            ctypes.HRESULT,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_longlong,
            ctypes.c_longlong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRCDEFAULTQUALITY
        _check(
            initialize(AUDCLNT_SHAREMODE_SHARED, flags, 2000000, 0, mix_ptr, None),
            "IAudioClient.Initialize",
        )
        get_buf = _fn(client, 4, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))
        get_pad = _fn(client, 6, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))
        get_svc = _fn(client, 14, ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        start = _fn(client, 10, ctypes.HRESULT)
        self._buffer_frames = wintypes.UINT()
        _check(get_buf(ctypes.byref(self._buffer_frames)), "GetBufferSize")
        render = ctypes.c_void_p()
        _check(get_svc(ctypes.byref(IID_IAudioRenderClient), ctypes.byref(render)), "GetService render")
        self._get_pad = get_pad
        self._get_buffer = _fn(
            render, 3, ctypes.HRESULT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
        )
        self._release_buffer = _fn(render, 4, ctypes.HRESULT, wintypes.UINT, ctypes.c_uint)
        _check(start(), "IAudioClient.Start")
        self._client = client
        self._render = render
        self._mix_ptr = mix_ptr
        self.frame_bytes = self.channels * (4 if self.floating else self.bits // 8)
        print(
            f"WASAPI {chosen!r}  {self.rate}Hz ch={self.channels} "
            f"{'f32' if self.floating else f's{self.bits}'}  buf={self._buffer_frames.value}",
            flush=True,
        )

    def write_s16_mono(self, pcm: bytes, src_rate: int) -> None:
        if self.frame_bytes <= 0:
            return
        payload = _to_mix(pcm, src_rate, self.rate, self.channels, self.floating)
        frames = len(payload) // self.frame_bytes
        if frames <= 0:
            return
        pad = wintypes.UINT()
        if self._get_pad(ctypes.byref(pad)) < 0:
            return
        free = int(self._buffer_frames.value) - int(pad.value)
        if free < frames:
            frames = max(0, free)
            payload = payload[: frames * self.frame_bytes]
            if frames <= 0:
                return
        dest = ctypes.c_void_p()
        if self._get_buffer(frames, ctypes.byref(dest)) < 0 or not dest:
            return
        ctypes.memmove(dest, payload, frames * self.frame_bytes)
        self._release_buffer(frames, 0)


def _device_name(device: ctypes.c_void_p) -> str:
    open_store = _fn(
        device, 4, ctypes.HRESULT, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)
    )
    store = ctypes.c_void_p()
    if open_store(STGM_READ, ctypes.byref(store)) < 0:
        return ""
    get_value = _fn(store, 5, ctypes.HRESULT, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
    key = PROPERTYKEY(PKEY_NAME_FMTID, 14)
    var = PROPVARIANT()
    if get_value(ctypes.byref(key), ctypes.byref(var)) < 0:
        return ""
    if var.vt != VT_LPWSTR or not var.data:
        return ""
    return ctypes.wstring_at(var.data)


def _to_mix(pcm: bytes, src_rate: int, dst_rate: int, channels: int, floating: bool) -> bytes:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if src_rate != dst_rate and samples:
        n_out = max(1, int(round(len(samples) * dst_rate / src_rate)))
        last = len(samples) - 1
        scale = (len(samples) - 1) / max(1, n_out - 1)
        out = array.array("h")
        for i in range(n_out):
            pos = i * scale
            i0 = int(pos)
            i1 = i0 + 1 if i0 < last else last
            frac = pos - i0
            out.append(int(samples[i0] * (1.0 - frac) + samples[i1] * frac))
        samples = out
    if floating:
        floats = array.array("f")
        for sample in samples:
            value = sample / 32768.0
            if channels <= 1:
                floats.append(value)
            else:
                for _ in range(channels):
                    floats.append(value)
        return floats.tobytes()
    if channels <= 1:
        return samples.tobytes()
    stereo = array.array("h")
    for sample in samples:
        for _ in range(channels):
            stereo.append(sample)
    return stereo.tobytes()
