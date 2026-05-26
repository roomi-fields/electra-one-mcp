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


def upload_preset_simple(
    out_port: str,
    preset_json_bytes: bytes,
    lua_bytes: bytes | None,
    bank: int = 0,
    slot: int = 0,
    in_port: str | None = None,
    ack_timeout: float = 3.0,
) -> dict:
    """Upload a preset via the SIMPLE (documented & widely-used) path.

    This is the codepath both shipping CLIs use (johnnyclem/electra-one,
    elliotwoods/simularca-electra-one-plugin). The File Transfer cache API
    is documented for firmware / lua-modules / multi-file atomic deploys —
    NOT for preset+lua uploads, where it silently rolls back commits.

    Flow per docs.electra.one + forum thread #592 + the two reference impls:
      1. Switch Preset Slot: F0 00 21 45 09 08 bank slot F7   → ACK
      2. Upload Preset:      F0 00 21 45 01 01 <ascii-json> F7  → ACK
      3. Upload Lua Script:  F0 00 21 45 01 0C <ascii-lua> F7   → ACK (if lua present)
      4. Reload Preset Slot: F0 00 21 45 08 08 F7              → ACK
      5. caller can Get Active Preset (02 01) to verify

    Caveat: steps 2 & 3 each send the file as a SINGLE SysEx. On Windows the
    USB-MIDI 1.0 class driver fragments SysEx >~5 KB, so this works for
    presets/lua under that ceiling. For larger lua, callers should fall back
    to upload_lua_file_transfer (single-file FT with type:"lua").
    """
    import threading

    if in_port is None:
        in_port = out_port.replace("MIDIOUT", "MIDIIN")
        if in_port == out_port:
            in_port = "MIDIIN3 (Electra Controller)"

    pending: dict[int, dict] = {}
    pending_lock = threading.Lock()
    listener_stop = threading.Event()

    def _ack_listener():
        ports = list_ports()
        idx = _resolve_port(in_port, ports["inputs"])
        if idx is None:
            return
        CALLBACK_FUNCTION = 0x00030000
        MIM_LONGDATA = 0x3C4
        CallbackProto = ctypes.WINFUNCTYPE(None, c_void_p, c_uint, c_void_p, c_void_p, c_void_p)
        BUF_SIZE = 8192
        NUM_BUFS = 4
        headers = [MIDIHDR() for _ in range(NUM_BUFS)]
        buffers = [ctypes.create_string_buffer(BUF_SIZE) for _ in range(NUM_BUFS)]
        handle_l = c_void_p()

        def _arm(i):
            hdr = headers[i]
            hdr.lpData = ctypes.cast(buffers[i], ctypes.c_char_p)
            hdr.dwBufferLength = BUF_SIZE
            hdr.dwBytesRecorded = 0
            hdr.dwFlags = 0
            winmm.midiInPrepareHeader(handle_l, byref(hdr), sizeof(hdr))
            winmm.midiInAddBuffer(handle_l, byref(hdr), sizeof(hdr))

        def _cb(hMidi, wMsg, dwInst, dwP1, dwP2):
            if wMsg != MIM_LONGDATA or not dwP1:
                return
            hdr_ptr = ctypes.cast(dwP1, POINTER(MIDIHDR))
            hdr = hdr_ptr.contents
            n = hdr.dwBytesRecorded
            if n <= 0:
                return
            for i in range(NUM_BUFS):
                if ctypes.addressof(headers[i]) == ctypes.addressof(hdr):
                    raw = bytes(buffers[i].raw[:n])
                    # Parse ACK/NACK with optional txid
                    if len(raw) >= 7 and raw[1:5] == bytes([0x00, 0x21, 0x45, 0x7E]):
                        ok = raw[5] == 0x01
                        if len(raw) >= 9:
                            txid = raw[6] | (raw[7] << 7)
                        else:
                            txid = 0
                        with pending_lock:
                            pending[txid] = {"ok": ok, "raw": raw}
                    winmm.midiInUnprepareHeader(hMidi, hdr_ptr, sizeof(MIDIHDR))
                    _arm(i)
                    return

        cb = CallbackProto(_cb)
        rc = winmm.midiInOpen(byref(handle_l), idx, ctypes.cast(cb, c_void_p), None, CALLBACK_FUNCTION)
        if rc != 0:
            return
        try:
            for i in range(NUM_BUFS):
                _arm(i)
            winmm.midiInStart(handle_l)
            while not listener_stop.wait(0.02):
                pass
            winmm.midiInStop(handle_l)
            for hdr in headers:
                winmm.midiInUnprepareHeader(handle_l, byref(hdr), sizeof(hdr))
        finally:
            winmm.midiInClose(handle_l)

    listener_thread = threading.Thread(target=_ack_listener, daemon=True)
    listener_thread.start()
    time.sleep(0.1)

    _tx = [0]

    def _next_tx():
        _tx[0] = (_tx[0] + 1) & 0x3FFF
        return _tx[0]

    def _send_and_wait(cmd_body: bytes, step: str) -> dict:
        txid = _next_tx()
        lsb = txid & 0x7F
        msb = (txid >> 7) & 0x7F
        blob = bytes([0xF0, 0x00, 0x21, 0x45, 0x00, lsb, msb]) + cmd_body + bytes([0xF7])
        ports = list_ports()
        idx = _resolve_port(out_port, ports["outputs"])
        if idx is None:
            return {"ok": False, "step": step, "error": "out port not found"}
        handle = c_void_p()
        rc = winmm.midiOutOpen(byref(handle), idx, None, None, 0)
        if rc != 0:
            return {"ok": False, "step": step, "rc": rc, "error": "midiOutOpen"}
        try:
            rc = _send_one_chunk(handle, blob)
        finally:
            winmm.midiOutClose(handle)
        if rc != 0:
            return {"ok": False, "step": step, "rc": rc}
        deadline = time.monotonic() + ack_timeout
        while time.monotonic() < deadline:
            with pending_lock:
                if txid in pending:
                    r = pending.pop(txid)
                    return {"ok": r["ok"], "step": step, "txid": txid, "ack_hex": r["raw"].hex(),
                            "sent_bytes": len(blob)}
            time.sleep(0.005)
        return {"ok": False, "step": step, "txid": txid, "error": "ACK timeout",
                "sent_bytes": len(blob)}

    try:
        # 1. Switch slot
        r = _send_and_wait(bytes([0x09, 0x08, bank & 0x7F, slot & 0x7F]), "switch_slot")
        if not r["ok"]:
            return r

        # 2. Upload Preset (preset.json, no lua field)
        r = _send_and_wait(bytes([0x01, 0x01]) + preset_json_bytes, "upload_preset")
        if not r["ok"]:
            return r

        # 3. Upload Lua (if present and small enough). Caller should pre-check
        # size and use FT API for the lua if needed.
        if lua_bytes is not None and len(lua_bytes) > 0:
            r = _send_and_wait(bytes([0x01, 0x0C]) + lua_bytes, "upload_lua")
            if not r["ok"]:
                return r

        # 4. Force reload via slot-switch trick:
        #    08 08 Reload Preset Slot often NACKs on this firmware. Going to a
        #    different slot then back forces the device to fully tear down the
        #    runtime and re-init from disk (lua + preset together). The
        #    "neighbour" slot we visit briefly is bank/(slot XOR 1) — that
        #    keeps us within the same bank and only flips one bit.
        neighbour = slot ^ 1 if slot < 11 else slot - 1
        _send_and_wait(bytes([0x09, 0x08, bank & 0x7F, neighbour & 0x7F]), "switch_away")
        time.sleep(0.15)
        r = _send_and_wait(bytes([0x09, 0x08, bank & 0x7F, slot & 0x7F]), "switch_back")

        return {
            "ok": True, "via": "simple-01-01-and-01-0c",
            "bank": bank, "slot": slot,
            "preset_size": len(preset_json_bytes),
            "lua_size": len(lua_bytes) if lua_bytes else 0,
        }
    finally:
        listener_stop.set()
        listener_thread.join(timeout=2.0)


def upload_preset_file_transfer(
    out_port: str,
    preset_json_bytes: bytes,
    bank: int = 0,
    slot: int = 0,
    chunk_bytes: int = 1024,
    in_port: str | None = None,
    verify_ack: bool = True,
    ack_timeout: float = 3.0,
    use_tx_id: bool = True,
    lua_bytes: bytes | None = None,
) -> dict:
    """Upload a preset using Electra's File Transfer SysEx API.

    Each individual SysEx command stays well under the Windows USB-MIDI driver
    buffer limit. With `verify_ack=True` we send each command with a unique
    transaction id and wait for the device's ACK (or NACK) on the CTRL input
    port before sending the next command.

    Protocol (each command includes a 3-byte txid wrapper: 0x00 <lsb> <msb>):
      1. Open cache:    F0 00 21 45 00 <tx> <tx> 01 2D F7
      2. Register file: F0 00 21 45 00 <tx> <tx> 01 2E <fid> <s0..s3> F7
      3. Send chunks:   F0 00 21 45 00 <tx> <tx> 01 2F <fid> <data> F7
      4. Commit:        F0 00 21 45 00 <tx> <tx> 04 2D <commit-json> F7

    ACK format (firmware ≥ 4.0): F0 00 21 45 7E 01 <tx-lsb> <tx-msb> F7
    NACK: F0 00 21 45 7E 00 <tx-lsb> <tx-msb> F7
    """
    import hashlib, json as _json, threading

    # Build list of files to register/transfer
    files_to_send = [(1, "preset", preset_json_bytes, hashlib.md5(preset_json_bytes).hexdigest())]
    if lua_bytes is not None:
        files_to_send.append((2, "lua", lua_bytes, hashlib.md5(lua_bytes).hexdigest()))

    # backward-compat for diag fields
    file_id = 1
    size = len(preset_json_bytes)
    md5 = hashlib.md5(preset_json_bytes).hexdigest()

    if verify_ack and in_port is None:
        # Default: same port number on the input side
        in_port = out_port.replace("MIDIOUT", "MIDIIN")
        if in_port == out_port:  # no rename happened (e.g. "Electra Controller")
            # use the matching MIDIIN3 default
            in_port = "MIDIIN3 (Electra Controller)"

    # ---- async listener that captures ACK/NACK and parks them in a dict ----
    pending: dict[int, dict] = {}      # txid -> {"ok": bool, "raw": bytes}
    pending_lock = threading.Lock()
    listener_stop = threading.Event()

    def _ack_listener():
        ports = list_ports()
        idx = _resolve_port(in_port, ports["inputs"])
        if idx is None:
            return

        CALLBACK_FUNCTION = 0x00030000
        MIM_LONGDATA = 0x3C4
        CallbackProto = ctypes.WINFUNCTYPE(None, c_void_p, c_uint, c_void_p, c_void_p, c_void_p)
        BUF_SIZE = 8192
        NUM_BUFS = 4
        headers = [MIDIHDR() for _ in range(NUM_BUFS)]
        buffers = [ctypes.create_string_buffer(BUF_SIZE) for _ in range(NUM_BUFS)]
        handle_l = c_void_p()

        def _arm(i):
            hdr = headers[i]
            hdr.lpData = ctypes.cast(buffers[i], ctypes.c_char_p)
            hdr.dwBufferLength = BUF_SIZE
            hdr.dwBytesRecorded = 0
            hdr.dwFlags = 0
            winmm.midiInPrepareHeader(handle_l, byref(hdr), sizeof(hdr))
            winmm.midiInAddBuffer(handle_l, byref(hdr), sizeof(hdr))

        def _cb(hMidi, wMsg, dwInst, dwP1, dwP2):
            if wMsg != MIM_LONGDATA or not dwP1:
                return
            hdr_ptr = ctypes.cast(dwP1, POINTER(MIDIHDR))
            hdr = hdr_ptr.contents
            n = hdr.dwBytesRecorded
            if n <= 0:
                return
            for i in range(NUM_BUFS):
                if ctypes.addressof(headers[i]) == ctypes.addressof(hdr):
                    raw = bytes(buffers[i].raw[:n])
                    _classify_and_park(raw)
                    winmm.midiInUnprepareHeader(hMidi, hdr_ptr, sizeof(MIDIHDR))
                    _arm(i)
                    return

        # Capture EVERY SysEx received for diagnostics, even non-ACK
        all_received: list[bytes] = []

        def _classify_and_park(raw: bytes):
            all_received.append(raw)
            # Expected ACK/NACK: F0 00 21 45 7E <01|00> <lsb> <msb> F7
            if len(raw) < 9 or raw[0] != 0xF0 or raw[-1] != 0xF7:
                return
            if raw[1:5] != bytes([0x00, 0x21, 0x45, 0x7E]):
                return
            ok = raw[5] == 0x01
            txid = raw[6] | (raw[7] << 7)
            with pending_lock:
                pending[txid] = {"ok": ok, "raw": raw}

        cb = CallbackProto(_cb)
        rc = winmm.midiInOpen(byref(handle_l), idx, ctypes.cast(cb, c_void_p), None, CALLBACK_FUNCTION)
        if rc != 0:
            return
        try:
            for i in range(NUM_BUFS):
                _arm(i)
            winmm.midiInStart(handle_l)
            while not listener_stop.wait(0.02):
                pass
            winmm.midiInStop(handle_l)
            for hdr in headers:
                winmm.midiInUnprepareHeader(handle_l, byref(hdr), sizeof(hdr))
        finally:
            winmm.midiInClose(handle_l)

    listener_thread = None
    if verify_ack:
        listener_thread = threading.Thread(target=_ack_listener, daemon=True)
        listener_thread.start()
        time.sleep(0.1)  # let the listener arm itself

    # ---- helpers to send + wait ----
    _next_tx = [0]

    def _alloc_tx() -> int:
        _next_tx[0] = (_next_tx[0] + 1) & 0x3FFF  # 14-bit (two 7-bit bytes)
        return _next_tx[0]

    def _wrap_with_tx(cmd_body: bytes, txid: int) -> bytes:
        if not use_tx_id:
            # Legacy framing: no tx id
            return bytes([0xF0, 0x00, 0x21, 0x45]) + cmd_body + bytes([0xF7])
        lsb = txid & 0x7F
        msb = (txid >> 7) & 0x7F
        return (bytes([0xF0, 0x00, 0x21, 0x45, 0x00, lsb, msb])
                + cmd_body
                + bytes([0xF7]))

    def _send_and_wait(cmd_body: bytes, step_name: str) -> dict:
        txid = _alloc_tx()
        blob = _wrap_with_tx(cmd_body, txid)
        ports = list_ports()
        idx = _resolve_port(out_port, ports["outputs"])
        if idx is None:
            return {"ok": False, "step": step_name, "error": "out port not found"}
        handle = c_void_p()
        rc = winmm.midiOutOpen(byref(handle), idx, None, None, 0)
        if rc != 0:
            return {"ok": False, "step": step_name, "rc": rc, "error": "midiOutOpen"}
        try:
            rc = _send_one_chunk(handle, blob)
        finally:
            winmm.midiOutClose(handle)
        if rc != 0:
            return {"ok": False, "step": step_name, "rc": rc, "txid": txid}

        if not verify_ack:
            return {"ok": True, "step": step_name, "txid": txid}

        deadline = time.monotonic() + ack_timeout
        while time.monotonic() < deadline:
            with pending_lock:
                if txid in pending:
                    result = pending.pop(txid)
                    return {
                        "ok": result["ok"], "step": step_name, "txid": txid,
                        "ack_hex": result["raw"].hex(),
                    }
            time.sleep(0.01)
        with pending_lock:
            other_acks = {k: v["raw"].hex() for k, v in pending.items()}
        return {
            "ok": False, "step": step_name, "txid": txid,
            "error": "ACK timeout",
            "sent_hex": blob.hex(),
            "other_acks_received": other_acks,
        }

    step_log = []

    try:
        # 1. Open cache
        r = _send_and_wait(bytes([0x01, 0x2D]), "open_cache")
        step_log.append(r)
        if not r["ok"]:
            return r

        # 2. Register all files (one Register command per file)
        for fid, ftype, fbytes, fmd5 in files_to_send:
            fsize = len(fbytes)
            s0 = fsize & 0x7F
            s1 = (fsize >> 7) & 0x7F
            s2 = (fsize >> 14) & 0x7F
            s3 = (fsize >> 21) & 0x7F
            r = _send_and_wait(
                bytes([0x01, 0x2E, fid, s0, s1, s2, s3]),
                f"register_{ftype}",
            )
            step_log.append(r)
            if not r["ok"]:
                return r

        # 3. Send chunks for each file (interleave them per file id)
        chunks_sent = 0
        for fid, ftype, fbytes, fmd5 in files_to_send:
            fsize = len(fbytes)
            for offset in range(0, fsize, chunk_bytes):
                chunk = fbytes[offset:offset + chunk_bytes]
                if any(b >= 0x80 for b in chunk):
                    return {"ok": False, "step": "chunk", "error": "non-7bit byte", "file": ftype, "offset": offset}
                cmd = bytes([0x01, 0x2F, fid]) + chunk
                r = _send_and_wait(cmd, f"chunk_{ftype}_{chunks_sent}")
                if not r["ok"]:
                    r["chunk_index"] = chunks_sent
                    r["file"] = ftype
                    return r
                chunks_sent += 1
                time.sleep(0.01)

        # 4. Commit — one entry per file
        files_list = []
        for fid, ftype, fbytes, fmd5 in files_to_send:
            files_list.append({
                "id": fid, "location": "slots", "type": ftype,
                "bankNumber": bank, "slot": slot, "md5": fmd5,
            })
        commit = {"files": files_list}
        commit_json = _json.dumps(commit, separators=(",", ":")).encode("utf-8")
        r = _send_and_wait(bytes([0x04, 0x2D]) + commit_json, "commit")
        step_log.append(r)
        if not r["ok"]:
            return r

        # 5. Switch active slot pointer to where we wrote the file. No-op if
        #    we were already there, but ensures the next reload reads the right
        #    file. The Switch command is `09 08 bank slot`.
        switch_cmd = bytes([0x09, 0x08, bank & 0x7F, slot & 0x7F])
        r = _send_and_wait(switch_cmd, "switch_preset")
        step_log.append(r)

        # 6. Reload the active preset slot — terminates the running instance
        #    and re-reads from disk. This is what makes the preset display on
        #    the device WITHOUT requiring a physical reboot. The simple
        #    `01 01 Upload Preset` SysEx triggers this implicitly, but the FT
        #    API requires an explicit reload after commit.
        #    Command: F0 00 21 45 08 08 F7  (no bank/slot, reloads active)
        reload_cmd = bytes([0x08, 0x08])
        r = _send_and_wait(reload_cmd, "reload_slot")
        step_log.append(r)

        # First-and-last-chunk diagnostics
        diag = {
            "open_cache_ack": step_log[0].get("ack_hex"),
            "register_ack": step_log[1].get("ack_hex"),
            "commit_ack": step_log[-3].get("ack_hex") if len(step_log) >= 3 else None,
            "switch_ack": step_log[-2].get("ack_hex") if len(step_log) >= 2 else None,
            "reload_ack": step_log[-1].get("ack_hex"),
        }
        return {
            "ok": True, "via": "file-transfer-api",
            "size": size, "chunks": chunks_sent, "md5": md5,
            "bank": bank, "slot": slot,
            "diag": diag,
        }
    finally:
        if listener_thread is not None:
            listener_stop.set()
            listener_thread.join(timeout=2.0)


def listen_sysex(port_name: str, seconds: float) -> dict:
    """Capture SysEx messages from an input port for `seconds`.

    Uses a CALLBACK_FUNCTION that copies the bytes into a Python list under a
    lock. The polling-based approach we tried earlier (reading MHDR_DONE)
    returned only F0 followed by zeros — the driver fills the buffer
    asynchronously and our reads raced with the write-back, so we got the
    first byte and stale zeros for the rest. The callback approach lets the
    driver fully finish before notifying us.
    """
    import threading

    ports = list_ports()
    idx = _resolve_port(port_name, ports["inputs"])
    if idx is None:
        return {"ok": False, "error": f"input port not found: {port_name!r}", "available": ports["inputs"]}

    CALLBACK_FUNCTION = 0x00030000
    MIM_LONGDATA = 0x3C4
    MIM_DATA = 0x3C3  # short messages, useful for verifying the listen pipe works at all
    captured: list[bytes] = []
    short_messages: list[int] = []
    lock = threading.Lock()

    # WINFUNCTYPE generates the proper Windows stdcall trampoline + holds the
    # GIL across boundary. The driver fires this on its own worker thread.
    CallbackProto = ctypes.WINFUNCTYPE(None, c_void_p, c_uint, c_void_p, c_void_p, c_void_p)

    BUF_SIZE = 8192
    NUM_BUFS = 4
    headers = [MIDIHDR() for _ in range(NUM_BUFS)]
    buffers = [ctypes.create_string_buffer(BUF_SIZE) for _ in range(NUM_BUFS)]
    handle = c_void_p()

    def _arm(i: int, do_prepare: bool = True):
        hdr = headers[i]
        hdr.lpData = ctypes.cast(buffers[i], ctypes.c_char_p)
        hdr.dwBufferLength = BUF_SIZE
        hdr.dwBytesRecorded = 0
        hdr.dwFlags = 0
        if do_prepare:
            winmm.midiInPrepareHeader(handle, byref(hdr), sizeof(hdr))
        winmm.midiInAddBuffer(handle, byref(hdr), sizeof(hdr))

    def _cb(hMidi, wMsg, dwInst, dwP1, dwP2):
        if wMsg == MIM_LONGDATA and dwP1:
            hdr_ptr = ctypes.cast(dwP1, POINTER(MIDIHDR))
            hdr = hdr_ptr.contents
            n = hdr.dwBytesRecorded
            if n > 0:
                # Read directly from the C buffer, NOT through hdr.lpData
                # (the latter is a c_char_p which auto-truncates at first NUL —
                # Electra SysEx contains 0x00 in the manufacturer id).
                # We need to find which buffer this header refers to and read
                # from the corresponding ctypes buffer.
                for i in range(NUM_BUFS):
                    if ctypes.addressof(headers[i]) == ctypes.addressof(hdr):
                        raw = bytes(buffers[i].raw[:n])
                        with lock:
                            captured.append(raw)
                        # Re-arm without re-preparing (driver keeps the prepare flag)
                        # Actually: per Win32 docs, after MIM_LONGDATA we need to
                        # unprepare then re-prepare to reuse the buffer.
                        winmm.midiInUnprepareHeader(hMidi, hdr_ptr, sizeof(MIDIHDR))
                        _arm(i, do_prepare=True)
                        return
        elif wMsg == MIM_DATA:
            # Short MIDI message — encoded in low 24 bits of dwP1 (or c_void_p value).
            # Useful purely as proof-of-life that the callback fires at all.
            try:
                val = ctypes.cast(dwP1, ctypes.c_void_p).value or 0
            except (TypeError, ValueError):
                val = 0
            with lock:
                short_messages.append(val & 0xFFFFFF)

    cb = CallbackProto(_cb)
    rc = winmm.midiInOpen(byref(handle), idx, ctypes.cast(cb, c_void_p), None, CALLBACK_FUNCTION)
    if rc != 0:
        return {"ok": False, "error": f"midiInOpen failed: rc={rc}"}

    try:
        for i in range(NUM_BUFS):
            _arm(i, do_prepare=True)
        winmm.midiInStart(handle)
        # Sleep in small slices so the GIL is regularly released to the cb thread.
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            time.sleep(0.01)
        winmm.midiInStop(handle)
        winmm.midiInReset(handle) if hasattr(winmm, "midiInReset") else None
        for hdr in headers:
            winmm.midiInUnprepareHeader(handle, byref(hdr), sizeof(hdr))
    finally:
        winmm.midiInClose(handle)

    msgs = []
    with lock:
        for raw in captured:
            text = raw.decode("utf-8", errors="ignore")
            msgs.append({"hex": raw.hex(), "text": text, "len": len(raw)})
        shorts = list(short_messages)
    return {
        "ok": True,
        "port": ports["inputs"][idx]["name"],
        "messages": msgs,
        "short_messages": [f"{m:06x}" for m in shorts[:50]],
    }


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
    up.add_argument("--no-verify-ack", action="store_true")
    up.add_argument("--no-tx-id", action="store_true", help="skip transaction id wrapping (legacy firmware compat)")
    up.add_argument("--mode", choices=["simple", "ft"], default="simple",
                    help="simple = 01 01 + 01 0C (the path the working CLIs use); "
                         "ft = File Transfer cache (open/register/chunks/commit). "
                         "default 'simple' per sourced research (forum #592, johnnyclem, elliotwoods).")

    args = ap.parse_args()

    if args.cmd == "list":
        result = list_ports()
        result["ok"] = True
    elif args.cmd == "send":
        result = send_sysex(args.port, args.payload, chunk_size=args.chunk_size)
    elif args.cmd == "listen":
        result = listen_sysex(args.port, args.seconds)
    elif args.cmd == "upload-preset":
        with open(args.preset, encoding="utf-8") as f:
            project = json.loads(f.read())

        # If this is a "tiles"-schema project (our widget repo format), convert
        # it to the "controls" format the firmware actually parses. The
        # converter mirrors app.electra.one's projectToPreset (see
        # server/preset_converter.py) and produces byte-identical output to
        # what the web editor sends.
        from preset_converter import split_preset_for_upload, project_to_preset
        is_tiles = "tiles" in project or "schemaVersion" in project
        if is_tiles:
            target = project.get("targetDevice", "mk2")
            preset_bytes, lua_bytes = split_preset_for_upload(project, target_device=target)
        else:
            # Already in controls format — just strip lua + minify
            lua_raw = project.pop("lua", "")
            lua_bytes = lua_raw.encode("ascii", errors="ignore") if (lua_raw and lua_raw.strip()) else None
            preset_bytes = json.dumps(project, separators=(",", ":"), ensure_ascii=True).encode("ascii")

        if args.mode == "simple":
            result = upload_preset_simple(
                args.port, preset_bytes, lua_bytes,
                bank=args.bank, slot=args.slot,
            )
        else:
            result = upload_preset_file_transfer(
                args.port, preset_bytes,
                bank=args.bank, slot=args.slot, chunk_bytes=args.chunk_bytes,
                verify_ack=not args.no_verify_ack,
                use_tx_id=not args.no_tx_id,
                lua_bytes=lua_bytes,
            )
    else:
        result = {"ok": False, "error": f"unknown cmd: {args.cmd}"}

    print(json.dumps(result))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
