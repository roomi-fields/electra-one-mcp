"""MCP server for the Electra One plugin.

Exposes nine tools that combine USB SysEx push (via mido + python-rtmidi),
MIDI log capture, doc search, validation, bundling, and emulator screenshots.

Run with:  python server/server.py  (after `pip install mcp mido python-rtmidi`)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.stderr.write(
        "missing dependency: install the official MCP SDK first\n"
        "  pip install mcp\n"
    )
    raise

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DOCS_MD = PLUGIN_ROOT / "docs" / "md"
DOCS_STRUCTURED = PLUGIN_ROOT / "docs" / "structured"

mcp = FastMCP("electra-one")


# ---------- MIDI helpers (mido + python-rtmidi, no external binary) ----------

def _get_mido():
    """Lazy-import mido so the rest of the server works without it."""
    try:
        import mido as _m
        # Pin the rtmidi backend explicitly; mido's auto-detection sometimes
        # falls back to a stub that silently returns no ports.
        _m.set_backend("mido.backends.rtmidi")
        return _m
    except ImportError:
        return None
    except Exception:
        # Backend loaded but ALSA/CoreMIDI/WinMM unavailable. Return the module
        # anyway; the caller surfaces the runtime error on get_*_names().
        import mido as _m
        return _m


def _find_electra_port(direction: str = "output") -> str | None:
    m = _get_mido()
    if m is None:
        return None
    try:
        ports = m.get_output_names() if direction == "output" else m.get_input_names()
    except Exception:
        return None
    for p in ports:
        if "electra" in p.lower():
            return p
    return None


_MIDI_BACKEND_HINT = (
    "MIDI backend (rtmidi) failed to enumerate ports. On WSL this usually "
    "means /dev/snd/seq is missing — forward the device with usbipd-win and "
    "make sure ALSA is set up (the same mechanism osc-bridge uses)."
)


# ---------- tools ----------

@mcp.tool()
def push_to_device(preset_path: str, port: str | None = None) -> dict[str, Any]:
    """Upload a preset JSON to the Electra One's currently selected slot via USB SysEx.

    Sends `F0 00 21 45 01 01 <preset-json-bytes> F7` directly through the OS
    MIDI stack (mido + python-rtmidi). No external binary required.

    Args:
        preset_path: Filesystem path to the preset.json (or demo.preset.json).
        port: Optional MIDI output port name; auto-detected (matches "electra") if omitted.
    """
    m = _get_mido()
    if m is None:
        return {"ok": False, "error": "install python-mido and python-rtmidi: pip install mido python-rtmidi"}

    p = Path(preset_path)
    if not p.exists():
        return {"ok": False, "error": f"preset not found: {preset_path}"}

    preset_json = p.read_text(encoding="utf-8")
    minified = json.dumps(json.loads(preset_json), separators=(",", ":"))
    # SysEx data: mido frames with F0/F7 around `data`
    payload = [0x00, 0x21, 0x45, 0x01, 0x01] + list(minified.encode("utf-8"))

    target = port or _find_electra_port("output")
    if not target:
        try:
            available = m.get_output_names()
        except Exception as e:
            return {"ok": False, "error": _MIDI_BACKEND_HINT + f"  raw error: {e}"}
        return {"ok": False, "error": "no Electra MIDI port found", "available_ports": available}

    try:
        with m.open_output(target) as out:
            out.send(m.Message("sysex", data=payload))
    except Exception as e:
        return {"ok": False, "error": f"send failed: {e}"}

    return {"ok": True, "port": target, "bytes_sent": len(payload) + 2}


@mcp.tool()
def get_device_logs(seconds: float = 5.0, port: str | None = None) -> dict[str, Any]:
    """Listen on the CTRL port for `lua:` log messages from the device.

    Captures the device's print() output and fatal-error stack traces for the
    given number of seconds. Uses mido + python-rtmidi.

    Args:
        seconds: How long to listen (default 5).
        port: Optional MIDI input port name; auto-detected (matches "electra") if omitted.
    """
    m = _get_mido()
    if m is None:
        return {"ok": False, "error": "install python-mido and python-rtmidi: pip install mido python-rtmidi"}

    target = port or _find_electra_port("input")
    if not target:
        try:
            available = m.get_input_names()
        except Exception as e:
            return {"ok": False, "error": _MIDI_BACKEND_HINT + f"  raw error: {e}"}
        return {"ok": False, "error": "no Electra MIDI input port found", "available_ports": available}

    lines: list[str] = []
    end = time.monotonic() + float(seconds)
    try:
        with m.open_input(target) as inp:
            while time.monotonic() < end:
                msg = inp.poll()
                if msg is None:
                    time.sleep(0.005)
                    continue
                if msg.type != "sysex":
                    continue
                data = bytes(msg.data)
                text = data.decode("utf-8", errors="ignore").strip("\x00\x01\x02\x03\x04")
                if "lua:" in text:
                    idx = text.index("lua:")
                    text = text[idx:]
                lines.append(text)
    except Exception as e:
        return {"ok": False, "error": f"listen failed: {e}"}

    return {"ok": True, "port": target, "seconds": seconds, "lines": lines}


@mcp.tool()
def device_status(port: str | None = None) -> dict[str, Any]:
    """Query the device's firmware version, current preset, and MIDI port.

    Sends a `request electra info` SysEx and listens briefly for the reply
    (`F0 00 21 45 02 7F F7` → device responds with info SysEx).
    """
    m = _get_mido()
    if m is None:
        return {"ok": False, "error": "install python-mido and python-rtmidi"}

    out_port = port or _find_electra_port("output")
    in_port = port or _find_electra_port("input")
    if not out_port or not in_port:
        try:
            outs = m.get_output_names()
            ins = m.get_input_names()
        except Exception as e:
            return {"ok": False, "error": _MIDI_BACKEND_HINT + f"  raw error: {e}"}
        return {"ok": False, "error": "no Electra port found", "outputs": outs, "inputs": ins}

    request = [0x00, 0x21, 0x45, 0x02, 0x7F]
    reply_bytes: list[int] = []
    try:
        with m.open_input(in_port) as inp, m.open_output(out_port) as outp:
            outp.send(m.Message("sysex", data=request))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                msg = inp.poll()
                if msg and msg.type == "sysex":
                    reply_bytes = list(msg.data)
                    break
                time.sleep(0.005)
    except Exception as e:
        return {"ok": False, "error": f"status query failed: {e}"}

    if not reply_bytes:
        return {"ok": False, "error": "device did not respond within 2 s"}

    # Reply payload starts with 00 21 45 02 7F then a JSON or text string.
    # Strip the manufacturer/header prefix if present and try to decode as text.
    body = bytes(reply_bytes)
    for prefix in (bytes([0x00, 0x21, 0x45, 0x02, 0x7F]), bytes([0x00, 0x21, 0x45])):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break
    text = body.decode("utf-8", errors="replace").strip("\x00\x01\x02\x03")
    info: Any = text
    try:
        info = json.loads(text)
    except Exception:
        pass
    return {"ok": True, "port_in": in_port, "port_out": out_port, "info": info}


@mcp.tool()
def search_docs(query: str, kind: str = "all", limit: int = 8) -> dict[str, Any]:
    """Full-text search the mirrored docs.electra.one documentation.

    Args:
        query: Search terms (case-insensitive).
        kind: Section filter — `all`, `api`, `luacourse`, `troubleshooting`, `userguide`.
        limit: Max hits to return.
    """
    section_map = {
        "api": ["developers"],
        "luacourse": ["luacourse"],
        "troubleshooting": ["troubleshooting"],
        "userguide": ["userguide", "userguide-mk2", "userguide-mini"],
    }
    if kind == "all":
        allowed = None
    else:
        allowed = section_map.get(kind)
        if allowed is None:
            return {"ok": False, "error": f"unknown kind: {kind}"}

    needle = query.lower()
    hits = []
    for md_path in DOCS_MD.rglob("*.md"):
        rel = md_path.relative_to(DOCS_MD)
        if allowed is not None and not any(str(rel).startswith(a) for a in allowed):
            continue
        text = md_path.read_text(encoding="utf-8")
        idx = text.lower().find(needle)
        if idx == -1:
            continue
        start = max(0, idx - 80)
        end = min(len(text), idx + 200)
        snippet = text[start:end].replace("\n", " ")
        hits.append({"path": str(rel), "snippet": snippet})
        if len(hits) >= limit:
            break
    return {"ok": True, "query": query, "hits": hits}


@mcp.tool()
def get_api(symbol: str) -> dict[str, Any]:
    """Return the documented signature/params/returns/example for an Electra Lua API symbol.

    Reads from docs/structured/api.json (built by scripts/build-structured.py).
    """
    api_file = DOCS_STRUCTURED / "api.json"
    if not api_file.exists():
        return {
            "ok": False,
            "error": "docs/structured/api.json not built yet; run scripts/build-structured.py",
        }
    api = json.loads(api_file.read_text(encoding="utf-8"))
    info = api.get(symbol)
    if info is None:
        # Try fuzzy match
        cands = [k for k in api if symbol.lower() in k.lower()]
        return {"ok": False, "symbol": symbol, "candidates": cands[:10]}
    return {"ok": True, "symbol": symbol, "info": info}


@mcp.tool()
def list_constants(category: str = "all") -> dict[str, Any]:
    """List enum constants by category.

    Categories: `touch`, `events`, `midi`, `align`, `model`, `port`, `ptype`, `all`.
    """
    consts_file = DOCS_STRUCTURED / "constants.json"
    if not consts_file.exists():
        return {
            "ok": False,
            "error": "docs/structured/constants.json not built yet; run scripts/build-structured.py",
        }
    consts = json.loads(consts_file.read_text(encoding="utf-8"))
    if category == "all":
        return {"ok": True, "constants": consts}
    if category not in consts:
        return {"ok": False, "error": f"unknown category: {category}", "available": list(consts.keys())}
    return {"ok": True, "category": category, "constants": consts[category]}


@mcp.tool()
def validate_preset(preset_json: str) -> dict[str, Any]:
    """Check a preset JSON against schema + known-bad patterns.

    Surfaces gotchas that cause silent failures on device (drawText calls,
    bitwise ops at module load, IIFE wraps, local-graphics caching, etc.).

    Args:
        preset_json: Either a path to the .json file or the JSON string itself.
    """
    p = Path(preset_json)
    if p.exists():
        text = p.read_text(encoding="utf-8")
    else:
        text = preset_json

    try:
        proj = json.loads(text)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [f"invalid JSON: {e}"]}

    warnings: list[str] = []
    errors: list[str] = []

    # Required top-level fields
    for key in ("schemaVersion", "tiles", "devices"):
        if key not in proj:
            errors.append(f"missing required top-level field: {key}")

    # Device needs instrumentId
    for dev in proj.get("devices", []):
        if "instrumentId" not in dev:
            warnings.append(
                f"device {dev.get('id', '?')} missing instrumentId — set to "
                '"generic-controls" for a no-op MIDI mapping'
            )

    # Check the Lua field for known anti-patterns
    lua_code = proj.get("lua", "")
    if "graphics.drawText" in lua_code:
        errors.append(
            "lua: graphics.drawText is not a real API — use graphics.print(x, y, text, w, alignment)"
        )
    if "(function()" in lua_code and "Theme = (function()" in lua_code:
        warnings.append(
            "lua: Theme is wrapped in an IIFE; upvalues silently break on device "
            "(rev 4.1.4). Use flat concatenation instead."
        )
    if "local g = graphics" in lua_code:
        warnings.append(
            "lua: caching `graphics` to a local variable breaks callbacks on device. "
            "Reference graphics directly."
        )
    # Bitwise at load: very approximate detection
    for op in (" >> ", " << ", " & 0x", " | 0x"):
        if op in lua_code:
            warnings.append(
                f"lua: bitwise operator '{op.strip()}' detected — confirm firmware Lua "
                "version supports Lua 5.3 ops; otherwise precompute at bundle time"
            )
            break

    # Tile sanity
    for tile in proj.get("tiles", []):
        if tile.get("type") == "custom":
            inputs = tile.get("inputs", [])
            if len(inputs) > 1:
                warnings.append(
                    f"tile '{tile.get('name')}': declares {len(inputs)} inputs but the "
                    "firmware ignores `inputs` for type:'custom' tiles (only one pot dispatched). "
                    "See forum #4172."
                )

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


@mcp.tool()
def bundle_widget(theme_path: str, primitive_paths: list[str], widget_path: str) -> dict[str, Any]:
    """Concatenate theme + primitives + widget into a flat Lua blob.

    Mirrors the rules learned the hard way: no IIFE wrap, strip return Theme,
    rewrite `local function NAME` → `function Theme.NAME` for primitives.

    Args:
        theme_path: Path to lib/theme.lua.
        primitive_paths: List of paths to lib/primitives/*.lua, in load order.
        widget_path: Path to the widget's widget.lua.
    """
    def read_lua(p: str) -> str:
        return Path(p).read_text(encoding="utf-8").rstrip()

    def strip_return(code: str, symbol: str) -> str:
        import re
        return re.sub(rf"\nreturn {symbol}\s*$", "", code)

    def attach_primitive(name: str, code: str) -> str:
        import re
        code = strip_return(code, name)
        return re.sub(
            rf"^local function {name}\(",
            f"function Theme.{name}(",
            code,
            count=1,
            flags=re.MULTILINE,
        )

    parts = ["-- === claude-electra-one bundled preset ==="]
    parts.append("-- Auto-generated; do not edit by hand.")
    parts.append("")
    parts.append("-- ----- theme -----")
    parts.append(strip_return(read_lua(theme_path), "Theme"))
    parts.append("")
    for prim_path in primitive_paths:
        name = Path(prim_path).stem
        parts.append(f"-- ----- primitive: {name} -----")
        parts.append(attach_primitive(name, read_lua(prim_path)))
        parts.append("")
    parts.append("-- ----- widget -----")
    parts.append(read_lua(widget_path))

    return {"ok": True, "lua": "\n".join(parts)}


@mcp.tool()
def screenshot_widget(slug: str, url: str = "http://localhost:8765/docs/emulator/") -> dict[str, Any]:
    """Run a headless Playwright capture of the emulator rendering the widget.

    Returns the absolute path to the generated PNG. Requires Playwright + a local
    HTTP server serving the emulator + the `widgets/<slug>/widget.lua` resolvable.

    Args:
        slug: The widget directory name (e.g., "note-list-16").
        url: Emulator URL (defaults to a local server at port 8765).
    """
    repo_screenshot = Path.cwd() / "scripts" / "screenshot.mjs"
    if not repo_screenshot.exists():
        return {
            "ok": False,
            "error": "scripts/screenshot.mjs not found in cwd — run from electraone-widgets",
        }
    try:
        result = subprocess.run(
            ["node", str(repo_screenshot), slug, f"--url={url}", "--wait=2000"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "screenshot timed out"}
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "preview_path": str(Path.cwd() / "widgets" / slug / "preview.png"),
    }


if __name__ == "__main__":
    mcp.run()
