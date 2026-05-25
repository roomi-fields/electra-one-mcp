"""Windows-side MIDI bridge for the Electra One MCP.

Used when the MCP server runs in WSL and cannot reach the USB device directly
(no /dev/snd/seq). The WSL-side server writes a SysEx payload to a file under
%TEMP% and invokes this script via `powershell.exe python win_bridge.py ...`.

This script uses only winmm.dll via ctypes — no rtmidi, mido, or any pip dep —
so it works on any vanilla Windows Python install.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from ctypes import POINTER, Structure, byref, c_uint, c_ushort, c_void_p, sizeof
from pathlib import Path

winmm = ctypes.WinDLL("winmm")


class MIDIHDR(Structure):
    _fields_ = [
        ("lpData", ctypes.c_char_p),
        ("dwBufferLength", c_uint),
        ("dwBytesRecorded", c_uint),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", c_uint),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
        ("dwOffset", c_uint),
        ("dwReserved", c_void_p * 8),
    ]


class MIDIOUTCAPSW(Structure):
    _fields_ = [
        ("wMid", c_ushort),
        ("wPid", c_ushort),
        ("vDriverVersion", c_uint),
        ("szPname", ctypes.c_wchar * 32),
        ("wTechnology", c_ushort),
        ("wVoices", c_ushort),
        ("wNotes", c_ushort),
        ("wChannelMask", c_ushort),
        ("dwSupport", c_uint),
    ]


class MIDIINCAPSW(Structure):
    _fields_ = [
        ("wMid", c_ushort),
        ("wPid", c_ushort),
        ("vDriverVersion", c_uint),
        ("szPname", ctypes.c_wchar * 32),
        ("dwSupport", c_uint),
    ]


winmm.midiOutGetNumDevs.restype = c_uint
winmm.midiInGetNumDevs.restype = c_uint
winmm.midiOutGetDevCapsW.argtypes = [c_void_p, POINTER(MIDIOUTCAPSW), c_uint]
winmm.midiOutGetDevCapsW.restype = c_uint
winmm.midiInGetDevCapsW.argtypes = [c_void_p, POINTER(MIDIINCAPSW), c_uint]
winmm.midiInGetDevCapsW.restype = c_uint
winmm.midiOutOpen.argtypes = [POINTER(c_void_p), c_uint, c_void_p, c_void_p, c_uint]
winmm.midiOutOpen.restype = c_uint
winmm.midiOutClose.argtypes = [c_void_p]
winmm.midiOutClose.restype = c_uint
winmm.midiOutPrepareHeader.argtypes = [c_void_p, POINTER(MIDIHDR), c_uint]
winmm.midiOutPrepareHeader.restype = c_uint
winmm.midiOutUnprepareHeader.argtypes = [c_void_p, POINTER(MIDIHDR), c_uint]
winmm.midiOutUnprepareHeader.restype = c_uint
winmm.midiOutLongMsg.argtypes = [c_void_p, POINTER(MIDIHDR), c_uint]
winmm.midiOutLongMsg.restype = c_uint
winmm.midiInOpen.argtypes = [POINTER(c_void_p), c_uint, c_void_p, c_void_p, c_uint]
winmm.midiInOpen.restype = c_uint
winmm.midiInStart.argtypes = [c_void_p]
winmm.midiInStart.restype = c_uint
winmm.midiInStop.argtypes = [c_void_p]
winmm.midiInStop.restype = c_uint
winmm.midiInClose.argtypes = [c_void_p]
winmm.midiInClose.restype = c_uint
winmm.midiInAddBuffer.argtypes = [c_void_p, POINTER(MIDIHDR), c_uint]
winmm.midiInAddBuffer.restype = c_uint
winmm.midiInPrepareHeader.argtypes = [c_void_p, POINTER(MIDIHDR), c_uint]
winmm.midiInPrepareHeader.restype = c_uint
winmm.midiInUnprepareHeader.argtypes = [c_void_p, POINTER(MIDIHDR), c_uint]
winmm.midiInUnprepareHeader.restype = c_uint


def list_ports() -> dict:
    n_out = winmm.midiOutGetNumDevs()
    outs = []
    for i in range(n_out):
        c = MIDIOUTCAPSW()
        if winmm.midiOutGetDevCapsW(c_void_p(i), byref(c), sizeof(c)) == 0:
            outs.append({"index": i, "name": c.szPname})
    n_in = winmm.midiInGetNumDevs()
    ins = []
    for i in range(n_in):
        c = MIDIINCAPSW()
        if winmm.midiInGetDevCapsW(c_void_p(i), byref(c), sizeof(c)) == 0:
            ins.append({"index": i, "name": c.szPname})
    return {"outputs": outs, "inputs": ins}


def _resolve_port(name: str, ports: list[dict]) -> int | None:
    """Match by exact name first, then by case-insensitive substring."""
    for p in ports:
        if p["name"] == name:
            return p["index"]
    for p in ports:
        if name.lower() in p["name"].lower():
            return p["index"]
    return None


def _send_one_chunk(handle, data: bytes, timeout: float = 30.0) -> int:
    """Send a single midiOutLongMsg call. Returns 0 on success, MMSYSERR rc otherwise."""
    buf = ctypes.create_string_buffer(data, len(data))
    hdr = MIDIHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
    hdr.dwBufferLength = len(data)
    hdr.dwBytesRecorded = len(data)
    hdr.dwFlags = 0
    rc = winmm.midiOutPrepareHeader(handle, byref(hdr), sizeof(hdr))
    if rc != 0:
        return rc
    rc = winmm.midiOutLongMsg(handle, byref(hdr), sizeof(hdr))
    if rc != 0:
        winmm.midiOutUnprepareHeader(handle, byref(hdr), sizeof(hdr))
        return rc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if hdr.dwFlags & 0x1:  # MHDR_DONE
            break
        time.sleep(0.001)
    winmm.midiOutUnprepareHeader(handle, byref(hdr), sizeof(hdr))
    return 0


def send_sysex(port_name: str, payload_path: str, chunk_size: int = 256) -> dict:
    """Send the full SysEx bytes file (must begin with F0 and end with F7).

    Many Windows USB MIDI drivers can't handle very large midiOutLongMsg
    buffers in a single call, so we split the SysEx into chunks of
    `chunk_size` bytes and emit each via its own midiOutLongMsg. The bytes
    themselves form a continuous MIDI stream so the device sees one SysEx.
    """
    data = Path(payload_path).read_bytes()
    if not data or data[0] != 0xF0 or data[-1] != 0xF7:
        return {"ok": False, "error": "payload must start with F0 and end with F7"}

    ports = list_ports()
    idx = _resolve_port(port_name, ports["outputs"])
    if idx is None:
        return {"ok": False, "error": f"output port not found: {port_name!r}", "available": ports["outputs"]}

    handle = c_void_p()
    rc = winmm.midiOutOpen(byref(handle), idx, None, None, 0)
    if rc != 0:
        return {"ok": False, "error": f"midiOutOpen failed: rc={rc}", "port_index": idx}

    chunks_sent = 0
    last_rc = 0
    try:
        if len(data) <= chunk_size:
            last_rc = _send_one_chunk(handle, data)
            chunks_sent = 1
        else:
            for offset in range(0, len(data), chunk_size):
                chunk = data[offset:offset + chunk_size]
                last_rc = _send_one_chunk(handle, chunk)
                chunks_sent += 1
                if last_rc != 0:
                    break
                # No inter-chunk sleep — earlier 2ms gap made big presets unparseable.
    finally:
        winmm.midiOutClose(handle)

    if last_rc != 0:
        return {
            "ok": False,
            "error": f"midiOut chunk failed: rc={last_rc}",
            "chunks_sent": chunks_sent,
            "port": ports["outputs"][idx]["name"],
        }
    return {
        "ok": True,
        "port": ports["outputs"][idx]["name"],
        "port_index": idx,
        "bytes": len(data),
        "chunks": chunks_sent,
        "chunk_size": chunk_size,
    }


def upload_preset_file_transfer(
    out_port: str,
    preset_json_bytes: bytes,
    bank: int = 0,
    slot: int = 0,
    chunk_bytes: int = 1024,
) -> dict:
    """Upload a preset using Electra's File Transfer SysEx API.

    Each individual SysEx command stays well under the Windows USB-MIDI driver
    buffer limit, so this works for any preset size. Protocol:

      1. Open cache:    F0 00 21 45 01 2D F7
      2. Register file: F0 00 21 45 01 2E <fid> <s0> <s1> <s2> <s3> F7
      3. Send chunks:   F0 00 21 45 01 2F <fid> <data 7-bit> F7  (repeated)
      4. Commit:        F0 00 21 45 04 2D <commit-json> F7

    The preset JSON is plain ASCII so we can use it as raw 7-bit data without
    re-encoding (every byte is already < 128).
    """
    import hashlib, json as _json
    file_id = 1
    size = len(preset_json_bytes)
    md5 = hashlib.md5(preset_json_bytes).hexdigest()

    def _send_blob(blob: bytes) -> int:
        ports = list_ports()
        idx = _resolve_port(out_port, ports["outputs"])
        if idx is None:
            return -1
        handle = c_void_p()
        rc = winmm.midiOutOpen(byref(handle), idx, None, None, 0)
        if rc != 0:
            return rc
        try:
            return _send_one_chunk(handle, blob)
        finally:
            winmm.midiOutClose(handle)

    # 1. open cache
    open_cache = bytes([0xF0, 0x00, 0x21, 0x45, 0x01, 0x2D, 0xF7])
    rc = _send_blob(open_cache)
    if rc != 0:
        return {"ok": False, "step": "open_cache", "rc": rc}
    time.sleep(0.05)

    # 2. register file (size as four 7-bit bytes, little-endian)
    s0 = size & 0x7F
    s1 = (size >> 7) & 0x7F
    s2 = (size >> 14) & 0x7F
    s3 = (size >> 21) & 0x7F
    register = bytes([0xF0, 0x00, 0x21, 0x45, 0x01, 0x2E, file_id, s0, s1, s2, s3, 0xF7])
    rc = _send_blob(register)
    if rc != 0:
        return {"ok": False, "step": "register", "rc": rc}
    time.sleep(0.05)

    # 3. send chunks
    chunks_sent = 0
    for offset in range(0, size, chunk_bytes):
        chunk = preset_json_bytes[offset:offset + chunk_bytes]
        # SysEx data must be 7-bit. Preset JSON is ASCII so already compliant.
        if any(b >= 0x80 for b in chunk):
            return {"ok": False, "step": "chunk", "error": "non-ASCII byte in preset"}
        sysex = bytes([0xF0, 0x00, 0x21, 0x45, 0x01, 0x2F, file_id]) + chunk + bytes([0xF7])
        rc = _send_blob(sysex)
        if rc != 0:
            return {"ok": False, "step": "chunk", "chunk_index": chunks_sent, "rc": rc}
        chunks_sent += 1
        time.sleep(0.005)

    # 4. commit
    commit = {"files": [{
        "id": file_id, "location": "slots", "type": "preset",
        "bankNumber": bank, "slot": slot, "md5": md5,
    }]}
    commit_json = _json.dumps(commit, separators=(",", ":")).encode("utf-8")
    sysex = bytes([0xF0, 0x00, 0x21, 0x45, 0x04, 0x2D]) + commit_json + bytes([0xF7])
    rc = _send_blob(sysex)
    if rc != 0:
        return {"ok": False, "step": "commit", "rc": rc, "commit_size": len(sysex)}

    return {
        "ok": True, "via": "file-transfer-api",
        "size": size, "chunks": chunks_sent, "md5": md5,
        "bank": bank, "slot": slot,
    }


def listen_sysex(port_name: str, seconds: float) -> dict:
    """Capture SysEx messages from an input port for `seconds`.

    Uses polling on the prepared buffers' MHDR_DONE flag (no Python callback,
    so it doesn't depend on the GIL releasing to a callback thread). When a
    buffer is filled, we copy its bytes out and re-arm it.
    """
    ports = list_ports()
    idx = _resolve_port(port_name, ports["inputs"])
    if idx is None:
        return {"ok": False, "error": f"input port not found: {port_name!r}", "available": ports["inputs"]}

    CALLBACK_NULL = 0x00000000

    handle = c_void_p()
    rc = winmm.midiInOpen(byref(handle), idx, None, None, CALLBACK_NULL)
    if rc != 0:
        return {"ok": False, "error": f"midiInOpen failed: rc={rc}"}

    captured: list[bytes] = []
    short_captured: list[int] = []
    BUF_SIZE = 8192
    NUM_BUFS = 4
    headers = [MIDIHDR() for _ in range(NUM_BUFS)]
    buffers = [ctypes.create_string_buffer(BUF_SIZE) for _ in range(NUM_BUFS)]

    def _prep_and_arm(i: int):
        hdr = headers[i]
        hdr.lpData = ctypes.cast(buffers[i], ctypes.c_char_p)
        hdr.dwBufferLength = BUF_SIZE
        hdr.dwBytesRecorded = 0
        hdr.dwFlags = 0
        winmm.midiInPrepareHeader(handle, byref(hdr), sizeof(hdr))
        winmm.midiInAddBuffer(handle, byref(hdr), sizeof(hdr))

    try:
        for i in range(NUM_BUFS):
            _prep_and_arm(i)
        winmm.midiInStart(handle)
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            any_done = False
            for i, hdr in enumerate(headers):
                if hdr.dwFlags & 0x1 and hdr.dwBytesRecorded > 0:  # MHDR_DONE
                    raw = ctypes.string_at(
                        ctypes.cast(hdr.lpData, c_void_p), hdr.dwBytesRecorded
                    )
                    captured.append(raw)
                    winmm.midiInUnprepareHeader(handle, byref(hdr), sizeof(hdr))
                    _prep_and_arm(i)
                    any_done = True
            if not any_done:
                time.sleep(0.005)
        winmm.midiInStop(handle)
        for hdr in headers:
            winmm.midiInUnprepareHeader(handle, byref(hdr), sizeof(hdr))
    finally:
        winmm.midiInClose(handle)

    msgs = []
    for raw in captured:
        text = raw.decode("utf-8", errors="ignore")
        msgs.append({"hex": raw.hex(), "text": text})
    return {"ok": True, "port": ports["inputs"][idx]["name"], "messages": msgs}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sp = sub.add_parser("send")
    sp.add_argument("--port", required=True)
    sp.add_argument("--payload", required=True, help="path to SysEx bytes (F0..F7)")
    sp.add_argument("--chunk-size", type=int, default=256)
    lp = sub.add_parser("listen")
    lp.add_argument("--port", required=True)
    lp.add_argument("--seconds", type=float, default=5.0)

    up = sub.add_parser("upload-preset")
    up.add_argument("--port", required=True)
    up.add_argument("--preset", required=True, help="path to preset JSON file")
    up.add_argument("--bank", type=int, default=0)
    up.add_argument("--slot", type=int, default=0)
    up.add_argument("--chunk-bytes", type=int, default=1024)

    args = ap.parse_args()

    if args.cmd == "list":
        result = list_ports()
        result["ok"] = True
    elif args.cmd == "send":
        result = send_sysex(args.port, args.payload, chunk_size=args.chunk_size)
    elif args.cmd == "listen":
        result = listen_sysex(args.port, args.seconds)
    elif args.cmd == "upload-preset":
        with open(args.preset, "rb") as f:
            preset_bytes = f.read()
        # minify by json round-trip to drop whitespace
        try:
            obj = json.loads(preset_bytes)
            preset_bytes = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        except json.JSONDecodeError:
            pass
        result = upload_preset_file_transfer(
            args.port, preset_bytes,
            bank=args.bank, slot=args.slot, chunk_bytes=args.chunk_bytes,
        )
    else:
        result = {"ok": False, "error": f"unknown cmd: {args.cmd}"}

    print(json.dumps(result))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
