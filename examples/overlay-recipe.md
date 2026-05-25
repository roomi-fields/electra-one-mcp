# Tutorial: define a custom step list via an overlay

NewIgnis's [forum spec for a 16-step arp/seq control](https://forum.electra.one/t/looking-for-a-16-step-custom-control-for-arp-and-seq/4425) calls for "all 16 parameters share the same overlay list". That phrasing maps to the Electra One concept of an **overlay** — a lookup table from a parameter value to a display label.

This recipe shows how to customise a 16-step note-list widget's labels without touching the widget code, by defining an `overlay` table on the lane.

## The pattern

The shipped `note-list-16` widget (in the sister repo `electraone-widgets/widgets/note-list-16/`) has a `lanes` table that holds per-lane state. Each lane can include an `overlay` field:

```lua
lanes[1] = {
  name = "SCALE", color = Theme.ACCENT,
  cells = { 0, 2, 4, 5, 7, 9, 11, 12, 0, 2, 4, 5, 7, 9, 11, 12 },
  paramBase = 0,
  overlay = {
    { value = 0,  label = "I"   },
    { value = 1,  label = "♭II" },
    { value = 2,  label = "II"  },
    { value = 3,  label = "♭III"},
    { value = 4,  label = "III" },
    { value = 5,  label = "IV"  },
    { value = 6,  label = "♭V"  },
    { value = 7,  label = "V"   },
    { value = 8,  label = "♭VI" },
    { value = 9,  label = "VI"  },
    { value = 10, label = "♭VII"},
    { value = 11, label = "VII" },
    { value = 12, label = "I+"  },
  },
}
```

Now cell display reads `I`, `II`, `♭III`, … instead of raw `0`, `2`, `3`, …. The cells still hold MIDI values 0..127 (so downstream MIDI routing is unchanged), only the label rendering goes through the overlay.

## Range matching

For continuous values (velocity, gate %, CC values), exact matching is impractical. Use `from`/`to` ranges:

```lua
overlay = {
  { from = 0,   to = 31,  label = "pp" },
  { from = 32,  to = 55,  label = "p"  },
  { from = 56,  to = 79,  label = "mp" },
  { from = 80,  to = 103, label = "mf" },
  { from = 104, to = 119, label = "f"  },
  { from = 120, to = 127, label = "ff" },
}
```

Exact-match wins over range-match when both are defined for the same value.

## Where to look

- Source of truth: `widgets/note-list-16/widget.lua` in the `electraone-widgets` repo. See the `overlayLabel(overlay, v)` helper and how `formatCell(lane, v)` falls back from `lane.overlay` to `lane.kind`.
- Generated docs: `electra.get_api("overlays.create")` will return the firmware-level overlay API (different mechanism — defines overlays in the preset JSON's top-level `overlays` array, suitable for native control labels). Our Lua-side overlay is a Lua-only convention that doesn't require firmware support.

## Why a Lua-side overlay instead of `overlays.create`

The firmware-level `overlays.get(id) / overlays.create(id, items)` returns a Lua **userdata** object, but **the API doesn't expose a `getLabel(value)` method**. You can attach an overlay to a native value (so the controller shows the right label without Lua), but you can't read those labels back from inside Lua. For custom-paint widgets we need to render the label ourselves, so we store the lookup table directly in Lua.

If you also want the label to flow through to native MIDI / SysEx callbacks (e.g., a paired native preview tile), define the overlay in both places — same items list duplicated.
