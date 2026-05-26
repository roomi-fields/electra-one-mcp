"""tiles → controls preset converter.

Ports the `projectToPreset` function from `app.electra.one`'s JS bundle
(0c61af0.js, lines ~127000-131000) so we can push our widget JSONs (which use
the "tiles" schema) directly via SysEx, the same way the web editor does.

Layout math (MK2 6×12 grid, 72 slots per page) is from the same bundle,
verified against a sniffed conversion: slotId=1, span=1, vspan=0 →
bounds=[181, 6, 158, 16] + potId=8. Exact match.
"""

from __future__ import annotations

import json
import re
from typing import Any


# --- MK2 layout helpers (mirror of the bundle's `l` object at offset ~159446)

class _MK2Layout:
    """6 cols × 12 rows = 72 slots per page. Even rows (y=0,2,4,...) are
    label/group rows, odd rows (y=1,3,...) are control rows."""

    @staticmethod
    def slot_to_page_id(slot_id: int) -> int:
        return slot_id // 72 + 1

    @staticmethod
    def slot_to_x(slot_id: int) -> int:
        return slot_id % 6

    @staticmethod
    def slot_to_y(slot_id: int) -> int:
        page = _MK2Layout.slot_to_page_id(slot_id)
        rel = slot_id - 72 * (page - 1)
        return rel // 6

    @staticmethod
    def slot_to_bounds(slot_id: int, span: int = 1, vspan: int = 0) -> list[int]:
        x = _MK2Layout.slot_to_x(slot_id)
        y = _MK2Layout.slot_to_y(slot_id)
        l = y // 2
        d = 20 + 167 * x
        if y % 2 == 0:
            # Group / label row
            w = 146 * span + 21 * (span - 1) + 12
            h = (90 * vspan - 6 - 3) if vspan > 0 else 16
            d -= 6
        else:
            # Control row
            w = 146
            h = 56
        y_px = 6 + 22 * ((y // 2) + (y % 2)) + 68 * l
        return [d, y_px, w, h]

    @staticmethod
    def slot_to_pot(slot_id: int) -> int:
        x = _MK2Layout.slot_to_x(slot_id)
        y = _MK2Layout.slot_to_y(slot_id)
        return x + 1 if y % 4 == 1 else x + 7

    @staticmethod
    def slot_to_set(slot_id: int) -> int:
        y = _MK2Layout.slot_to_y(slot_id)
        return y // 4 + 1

    @staticmethod
    def bounds_to_slot(bounds: list[int], page_id: int = 1) -> int:
        """Inverse of slot_to_bounds: figure out which slot a (x, y, w, h)
        control occupies. Mirrors `boundsToSlotId` from the bundle (offset
        ~159446). Used by preset_to_project to convert controls back to tiles."""
        x_px, y_px = bounds[0], bounds[1]
        col = x_px // 170
        # rowGroup: 0 for top half (y < 90), 1 for next, etc.
        row_section = (y_px - 6) // 90
        row_offset = 0 if (y_px - 6) % 90 == 0 else 1
        return 72 * (page_id - 1) + 6 * (2 * row_section + row_offset) + col


# --- mini layout (4 cols × 6 rows = 24 per page) for completeness

class _MiniLayout:
    @staticmethod
    def slot_to_page_id(slot_id: int) -> int:
        return slot_id // 24 + 1

    @staticmethod
    def slot_to_x(slot_id: int) -> int:
        return slot_id % 4

    @staticmethod
    def slot_to_y(slot_id: int) -> int:
        page = _MiniLayout.slot_to_page_id(slot_id)
        rel = slot_id - 24 * (page - 1)
        return rel // 4

    @staticmethod
    def slot_to_bounds(slot_id: int, span: int = 1, vspan: int = 0) -> list[int]:
        x = _MiniLayout.slot_to_x(slot_id)
        y = _MiniLayout.slot_to_y(slot_id)
        f = y // 2
        m = y // 2 + (y % 2)
        v = y % 2 == 0
        if y < 4:
            t_x = 20 + 196 * x
            t_y = 10 + 26 * m + 136 * f
            if v:
                w = 175 * span + 21 * (span - 1) + 8
                h = (162 * vspan - 7 - 3) if vspan > 0 else 16
                t_x -= 4
            else:
                w = 175
                h = 122
        else:
            if v:
                w = 108 * span + 21 * (span - 1) + 8
                h = 16
                t_x = 264 + 130 * x + 13
                t_y = 342
            else:
                t_x = 264 + 130 * x + 13
                t_y = 363
                w = 117
                h = 51
        return [t_x, t_y, w, h]

    @staticmethod
    def slot_to_pot(slot_id: int) -> int:
        # Mini-specific (bundle offset ~158365): different from MK2
        x = _MiniLayout.slot_to_x(slot_id)
        y = _MiniLayout.slot_to_y(slot_id)
        return x + 4 * ((y - 1) // 2) + 1

    @staticmethod
    def slot_to_set(slot_id: int) -> int:
        return 1


_LAYOUTS = {"mk2": _MK2Layout, "mini": _MiniLayout}


# --- tile → control conversion (mirrors `x(e, t)` at offset ~127000)

def _tile_to_control(tile: dict, layout, device_id_default: int | None = None) -> dict:
    """Convert one tile object into one control object."""
    slot_id = tile.get("slotId", 0)
    span = int(tile.get("span", 1) or 1)
    vspan = int(tile.get("vspan", 0) or 0)

    control: dict[str, Any] = {
        "id": tile.get("reference") or tile.get("id"),
        "type": tile.get("type"),
        "visible": tile.get("visible", True),
        "name": str(tile.get("name", "")).upper(),
        "color": tile.get("color") or "FFFFFF",
        "bounds": layout.slot_to_bounds(slot_id, span, vspan),
        "pageId": layout.slot_to_page_id(slot_id),
        "controlSetId": layout.slot_to_set(slot_id),
        "inputs": [{
            "potId": layout.slot_to_pot(slot_id),
            "valueId": tile.get("primaryValue") or "value",
        }],
    }
    # Optional fields the bundle passes through
    for key in ("mode", "variant"):
        if tile.get(key) is not None:
            control[key] = tile[key]

    # Values handling: the bundle copies values, ensures the single-value case
    # has id="value", and propagates the tile's deviceId into each message.
    values = tile.get("values")
    tile_device_id = tile.get("deviceId") or device_id_default
    if values:
        values_copy = [dict(v) for v in values]
        if len(values_copy) == 1 and not values_copy[0].get("id"):
            values_copy[0]["id"] = "value"
        for v in values_copy:
            msg = v.get("message")
            if isinstance(msg, dict) and tile_device_id:
                msg.setdefault("deviceId", tile_device_id)
                # The bundle converts "relative" to cc7+relative flag — replicate
                if msg.get("type") == "relative":
                    msg["type"] = "cc7"
                    msg["relative"] = True
        control["values"] = values_copy
    return control


def project_to_preset(project: dict, target_device: str = "mk2") -> dict:
    """Convert a `tiles`-schema project into the `controls`-schema preset
    that the device firmware actually parses.

    Mirrors the `projectToPreset` function from `app.electra.one`:
    - schemaVersion → version
    - id → projectId
    - tiles[] → controls[]
    - tile.slotId → control.bounds via layout math
    - drop targetDevice, categories, firstPageId, lua (lua goes via 01 0C)
    - keep pages, devices; add empty overlays + groups if missing.
    """
    layout = _LAYOUTS.get(target_device, _MK2Layout)

    devices = project.get("devices", [])
    primary_device_id = devices[0]["id"] if devices else 1

    tiles = project.get("tiles", []) or []
    controls = [
        _tile_to_control(t, layout, device_id_default=primary_device_id)
        for t in tiles
        if t and t.get("type") != "empty"
    ]

    # Pages: keep id+name, default name if missing
    pages = []
    for p in project.get("pages", []) or []:
        pages.append({"id": p.get("id"), "name": p.get("name") or f"Page {p.get('id')}"})
    if not pages:
        pages = [{"id": 1, "name": "Page 1"}]

    return {
        "version": 2,
        "name": project.get("name") or "Untitled",
        "projectId": project.get("id") or "",
        "pages": pages,
        "groups": project.get("groups", []) or [],
        "devices": devices or [{"id": 1, "name": "Local", "port": 1, "channel": 1}],
        "overlays": project.get("overlays", []) or [],
        "controls": controls,
    }


# --- Lua sanitisation: strip non-ASCII so SysEx 7-bit transport doesn't choke

_NON_ASCII = re.compile(rb"[\x80-\xff]+")


def lua_to_ascii(lua: str) -> bytes:
    """Encode Lua source to ASCII bytes. Decorative non-ASCII chars (em-dash,
    middle-dot, arrows etc. in comments) are dropped — the device only accepts
    7-bit data over SysEx and these characters don't affect Lua execution."""
    return lua.encode("ascii", errors="ignore")


def preset_to_project(preset: dict, target_device: str = "mk2") -> dict:
    """Convert a device-format preset (`controls` schema) BACK to our widget
    repo's `tiles` schema. Mirrors `presetToProject` from app.electra.one's
    bundle (offset ~134272 in 0c61af0.js).

    Use case: pull a preset off the device after editing in the web editor
    (or after another tool wrote it), and save it back into our repo's
    widget format for diffing / version control.

    Each control becomes one tile:
      - `id` becomes `reference` (numeric)
      - the tile gets a fresh string `id` (uuid-ish — pseudo-uuid based on
        original id since we don't ship the `uuid` package)
      - `bounds` is reverse-mapped to `slotId` via `boundsToSlotId`
      - `inputs`, `controlSetId`, `pageId` are dropped (the forward
        converter regenerates them from slotId)

    Groups become `type:"label"` tiles with a `span` computed from bounds.

    Devices are filtered to only those actually referenced by some control.
    Pages are filtered to those that hold at least one control.
    """
    layout = _LAYOUTS.get(target_device, _MK2Layout)

    def _control_to_tile(control: dict, *, is_label: bool = False) -> dict:
        t = dict(control)
        bounds = t.get("bounds")
        page_id = t.get("pageId", 1)
        if bounds:
            t["slotId"] = layout.bounds_to_slot(bounds, page_id)
        if "id" in t:
            t["reference"] = t["id"]
        # Fresh string id (the editor uses real UUIDs; we mimic with a
        # deterministic-but-unique-looking string so repeated pulls of the
        # same preset stay diff-friendly)
        if "reference" in t:
            t["id"] = f"control-{t['reference']}"

        # If the value carries an overlayId, denormalise the overlay items
        # back into the tile's value object (web editor does the same)
        if t.get("values") and isinstance(t["values"], list) and t["values"]:
            primary = t["values"][0]
            overlay_id = primary.get("overlayId") if isinstance(primary, dict) else None
            if overlay_id and isinstance(preset.get("overlays"), list):
                ov = next((o for o in preset["overlays"] if o.get("id") == overlay_id), None)
                if ov:
                    primary["textValues"] = ov.get("items", [])

        # Strip fields that the forward converter regenerates
        for k in ("controlSetId", "pageId", "inputs", "bounds"):
            t.pop(k, None)

        if is_label:
            t["type"] = "label"
            if bounds:
                t["span"] = max(1, bounds[2] // 146)

        return t

    tiles_from_controls = [_control_to_tile(c) for c in preset.get("controls", []) or []]
    tiles_from_groups   = [_control_to_tile(g, is_label=True) for g in preset.get("groups", []) or []]
    all_tiles = tiles_from_controls + tiles_from_groups

    # Filter devices: only those referenced by some tile
    device_ids_in_use = set()
    page_ids_in_use = set()
    for t in all_tiles:
        slot_id = t.get("slotId")
        if slot_id is not None:
            page_ids_in_use.add(layout.slot_to_page_id(slot_id))
        if t.get("deviceId"):
            device_ids_in_use.add(t["deviceId"])

    devices = [
        {**d, "instrumentId": d.get("instrumentId") or "generic-controls"}
        for d in (preset.get("devices") or [])
        if d.get("id") in device_ids_in_use or not device_ids_in_use
    ]

    # Filter pages to those that hold at least one tile
    pages_src = preset.get("pages") or [{"id": 1, "name": "Page 1"}]
    if isinstance(pages_src, dict):
        pages_src = list(pages_src.values())
    pages = [p for p in pages_src if p.get("id") in page_ids_in_use] or pages_src

    return {
        "schemaVersion": 2,
        "id": preset.get("projectId") or "",
        "version": preset.get("version", 2),
        "name": preset.get("name") or "Untitled",
        "targetDevice": target_device,
        "devices": devices,
        "tiles": all_tiles,
        "pages": pages,
    }


def split_preset_for_upload(project: dict, target_device: str = "mk2") -> tuple[bytes, bytes | None]:
    """Take a tiles-schema project, return (preset_json_bytes, lua_bytes_or_None).

    Both ready to be sent over SysEx via `01 01` and `01 0C` respectively.
    """
    lua_raw = project.get("lua") or ""
    lua_bytes = lua_to_ascii(lua_raw) if lua_raw.strip() else None

    # Remove lua before converting; preset.json has no `lua` field
    project_no_lua = {k: v for k, v in project.items() if k != "lua"}
    converted = project_to_preset(project_no_lua, target_device=target_device)
    preset_bytes = json.dumps(converted, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    return preset_bytes, lua_bytes


if __name__ == "__main__":
    import sys, argparse
    ap = argparse.ArgumentParser(description="Convert a tiles-schema project to a device-ready preset+lua")
    ap.add_argument("preset_path", help="path to demo.preset.json (tiles schema)")
    ap.add_argument("--out-preset", default=None)
    ap.add_argument("--out-lua", default=None)
    args = ap.parse_args()

    project = json.loads(open(args.preset_path, encoding="utf-8").read())
    target = project.get("targetDevice", "mk2")
    preset_bytes, lua_bytes = split_preset_for_upload(project, target_device=target)

    if args.out_preset:
        open(args.out_preset, "wb").write(preset_bytes)
        print(f"preset: {len(preset_bytes)} bytes → {args.out_preset}")
    else:
        print(f"preset: {len(preset_bytes)} bytes (use --out-preset to save)")
        print(json.dumps(json.loads(preset_bytes), indent=2)[:1000])
    if lua_bytes:
        if args.out_lua:
            open(args.out_lua, "wb").write(lua_bytes)
            print(f"lua: {len(lua_bytes)} bytes → {args.out_lua}")
        else:
            print(f"lua: {len(lua_bytes)} bytes (use --out-lua to save)")
