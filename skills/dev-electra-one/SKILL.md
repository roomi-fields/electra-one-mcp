---
name: dev-electra-one
description: |
  Use this skill BEFORE writing or modifying any Lua/preset code targeting an Electra One MIDI controller (MK2/Mini), or pushing a preset via Firestore PATCH. Activate whenever the user mentions Electra One, MK2, electraone-widgets, a .lua file under widgets/, lib/theme.lua, the preset JSON, app.electra.one, setPaintCallback, setTouchCallback, setPotCallback, or anything about pushing/uploading widgets to the device. Reading the project's HOWTO.md is mandatory before touching code — multiple sessions have re-paid hours of debug time rediscovering things already documented (graphics.print not drawText, 0xRRGGBB colours, WIDTH/HEIGHT constants, integer-coord requirement, etc.).
license: MIT
metadata:
  author: roomi-fields
  version: 1.0.0
  source: https://github.com/roomi-fields/electra-one-mcp
---

# dev-electra-one

You are about to work on the Electra One MK2 MIDI controller — a programmable
device whose Lua API and firmware behave in several non-obvious ways. This
skill exists because we have lost hours, multiple times, rediscovering things
that are already documented in `HOWTO.md` or in this skill. Do not start
typing Lua before you've read the relevant material.

**Trust the documented quirks. Most of them are silent failures: the widget
renders as a default blue fader fallback, with no error in the browser
console because the logger is off by default. By the time you "see" the
problem, your only feedback is a bug-report-shaped silence.**

## Always do this first

1. **Read the official doc via RTFM**, not via WebFetch. The
   `electraone-widgets` project workspace has a local mirror of
   `docs.electra.one` at `.electra-docs/md/` (79 pages, including the
   entire Lua crash course) indexed by RTFM. Use
   `mcp__rtfm__rtfm_search "<query>"` to find relevant sections,
   `mcp__rtfm__rtfm_expand` to read them. The official docs ARE the
   source of truth — most behaviours we previously thought were
   "undocumented gotchas" turn out to be explicitly described once you
   grep the right page.
2. **Open the project's `HOWTO.md`** sections 1 and 2 for the
   workflow we use (custom-paint pattern, Firestore PATCH push).
3. **Check `MEMORY.md`** entry `project_e1_device_gotchas` for any
   firmware-specific quirks learned that aren't in the docs.
4. Only **after** doing the above, write code.

If a piece of behaviour isn't covered in those documents and you have to
figure it out from scratch, **add it back to `HOWTO.md`** before
considering the work done. Don't invent gotchas from intuition — grep
the local doc mirror first.

## The Lua API on device (firmware 4.1.4, verified 2026-05-25)

These are the things the official docs at `docs.electra.one/developers/luaext.html`
either don't say or don't make obvious. Treat every one of these as a hard
constraint, not a recommendation — most cause silent fallback to the default
blue fader rendering.

### Text drawing

```lua
graphics.print(x, y, text, width, alignment)
-- alignment ∈ { LEFT, CENTER, RIGHT } (global constants)
```

`graphics.drawText` **does not exist**. Calling it raises
`attempt to call a nil value (field 'drawText')` at paint time. For natural
LEFT-aligned text where you've already computed x, pass a large width
(e.g. 9999) so it never truncates.

### Colour values

Pass `0xRRGGBB` 24-bit integers. The firmware (4.1.4+) handles the RGB565
conversion internally. Do **not** pre-convert to RGB565 — the v4.1.4 release
notes specifically fixed RGB888→RGB565 translation, so what worked before
might surprise you now.

```lua
Theme.ACCENT = 0xE5823E   -- correct
-- Theme.ACCENT = 0xE407   -- WRONG, this is RGB565 reinterpreted as RGB888 → blue-ish
```

### Integer coordinates

Every `graphics.fillRect / drawRect / drawLine / fillCircle / drawCircle /
print` argument must be an integer. A float coordinate raises
`number has no integer representation` and aborts the paint callback. Any
expression involving a `/ N` division produces a float — wrap it in
`math.floor(...)`. The `Theme.rect / outline / line` helpers in
`lib/theme.lua` already do this internally; coordinates passed direct to
`graphics.*` do not.

### Bounds and event types

```lua
local b = ctrl:getBounds()
local w, h = b[WIDTH], b[HEIGHT]      -- WIDTH=3, HEIGHT=4, X=1, Y=2
```

Touch event types are global constants:
**`DOWN`, `MOVE`, `UP`, `CLICK`, `DOUBLECLICK`**. Yes, `CLICK` and
`DOUBLECLICK` are dispatched natively by the firmware — you don't need to
implement a click-detection state machine in Lua. Touch coordinates
`event.x / event.y` arrive in **tile-local** coordinates (origin at the
top-left of the tile's bounds).

### Pots / encoders — the empirical truth (firmware 4.1.4)

```lua
ctrl:setPotCallback(function(ctrl, ev)
  -- ev.id    = 0-indexed pot number IN THE CALLBACK (0..11), even though
  --             the doc and the JSON `inputs[].potId` use 1..12. This
  --             off-by-one between doc and runtime is confirmed.
  -- ev.type  = DOWN | MOVE | UP   (same constants as touch)
  -- ev.delta = signed integer; 0 when no rotation (DOWN/UP frames)
end)
```

**`inputs[]` is IGNORED for `type:"custom"` tiles.** Tested 2026-05-25
on firmware 4.1.4: declaring 2 entries `{potId: 1, valueId: "step"}` +
`{potId: 2, valueId: "edit"}` on a custom tile produces NO Lua
callback events AND no USB MIDI traffic when the user rotates those
extra pots. The multi-input mechanism documented for ADSR/fader
controls (see `presetformat.md` "an ADSR control with multiple values
assigned") does NOT apply to custom controls. This is the exact
blocker for the NewIgnis spec — feature request #4172.

**Each custom tile receives exactly one pot.** Which pot it is depends
on the tile's `slotId`. Observed mapping on a default-configured MK2:
- tile in `slotId: 0` (top row, full-width) → receives `ev.id = 6`
  (= leftmost pot of the bottom physical row)
- tile in `slotId: 6` (second row, full-width) → receives `ev.id = 0`
  (= leftmost pot of the top physical row)

So the firmware delivers each tile's pot from the **OPPOSITE physical
row**. The other 10 pots produce nothing for this tile (no callback,
no MIDI). If "top pot controls top lane" matters for UX, route the
action manually in your potCallback (cross-dispatch):

```lua
local sourceId = ctrl:getId()
local targetId = (sourceId == 1) and 2 or 1
-- operate on lanes[targetId] / selectedStep[targetId]
```

**Encoder press not distinguishable from rotation start.**
`events.onPotTouchChange(potId, controlId, touched)` fires on
capacitive touch but cannot tell "pressed without turning" from
"started to turn". Forum threads #4116 and #4185 confirm: no
`onPotClick`. For a click-without-rotation gesture, track a
`rotatedDuringTouch` flag (set true on MOVE, reset on DOWN) and treat
UP-without-MOVE as click. Even this is fiddly — the user has to be
quite gentle to avoid an accidental tiny rotation.

### Observing parameter changes from Lua

`parameterMap.onChange` is a **global function you DEFINE** (like
`preset.onLoad`), not one you call:

```lua
function parameterMap.onChange(valueObjects, origin, midiValue)
  for i, vo in ipairs(valueObjects) do
    local ctrl = vo:getControl()
    print(string.format("changed: %s.%s = %d",
      ctrl:getName(), vo:getId(), midiValue))
  end
end
```

Trying to call `parameterMap.onChange(vo, callback)` raises
"attempt to call a nil value (field 'onChange')" — there is no such
method. The dispatch is automatic: the firmware invokes the global
when any ParameterMap entry changes (from MIDI in, from a pot mapped
via `inputs`, from `parameterMap.set`, etc.).

`origin` is one of the documented enum values (`ORIGIN_LUA`,
`ORIGIN_MIDI`, etc. — see `.electra-docs/md/developers/luaext.md` for
the full list). Use it to skip echoing your own writes.

### Touch on multi-tile presets

`setTouchCallback` fires reliably when the tile occupies the **slot** the
firmware expects. If you reposition a tile via `setBounds` to a different
visual area, the firmware still dispatches touches by `slotId`, not by the
new bounds. `event.x / event.y` will then arrive in the original slot's
coordinate space, hit-tests calculated against `ctrl:getBounds()` (which
returns your overridden bounds) will miss, and tap-to-select silently does
nothing. Resolution: pick `slotId` values that match where you want the
tile, and only use `setBounds` to *enlarge* (e.g. to stretch a 1-slot tile
to fullpage), not to *relocate*.

### Bundling theme + primitives + widget

The project's `scripts/bundle-preset.js` concatenates `lib/theme.lua` +
selected `lib/primitives/*.lua` + the widget's `widget.lua` into the
preset's `lua` field for device upload. Key rules learned the hard way:

- **No IIFE wrappers.** `Theme = (function() local Theme = {} ... return Theme end)()`
  fails silently on device: closures inside the IIFE lose their upvalues
  after the load chunk completes, callbacks fall back, blue bar appears.
  Use flat concatenation: `theme.lua` declares `Theme = Theme or {}` at the
  top, drops `return Theme`, and primitives rewrite `local function name`
  to `function Theme.name`.
- **No `local g = graphics` at module top level.** Same closure problem.
  Reference `graphics` directly inside each helper.
- **No bitwise operators** in code that runs at load time (top-level
  expressions, module constants). The Lua runtime on some firmware
  revisions doesn't parse `>>` `<<` `&` `|` (Lua < 5.3). If you need the
  RGB565 conversion, precompute the values in the bundler in Python/JS.

### Callbacks must be global

`paintLane`, `touchLane`, `potLane` must be declared as top-level globals
(`function paintLane(ctrl) ... end`), not as local closures captured by a
factory or IIFE. A `control:setPaintCallback(localFn)` where `localFn` is a
closure with upvalues falls back to default fader rendering after the chunk
loads. Per-tile state lives in global tables keyed by `ctrl:getId()`:

```lua
lanes        = { [1] = {...}, [2] = {...} }
selectedStep = { [1] = 1, [2] = 1 }
dragging     = { [1] = nil, [2] = nil }
muted        = { [1] = {}, [2] = {} }
```

### Preset JSON structure (schemaVersion 2 works for custom tiles)

Clone XT envelopes (`GK6wmbgvwM6S3GanpoN7`) as a template — its single
custom tile renders correctly on device. Minimum tile fields:

```json
{
  "id": "<uuid>",
  "reference": 1,
  "slotId": 0,
  "type": "custom",
  "deviceId": 1,
  "color": "F45C51",
  "name": "MyTile",
  "categoryId": "control",
  "values": [{"message": {"type":"virtual","deviceId":1,"parameterNumber":99}}],
  "visible": true,
  "span": "6",
  "vspan": 1
}
```

Device JSON needs `instrumentId: "generic-controls"`:

```json
"devices": [{"id":1, "name":"Generic MIDI", "instrumentId":"generic-controls", "port":1, "channel":1}]
```

- **`slotId` is 0-indexed.** Top-left = `slotId: 0`; second row left =
  `slotId: 6` on the MK2 6×6 grid.
- **`span:"6"` (string) only reliably enlarges the FIRST custom tile** in
  the JSON. A second tile with `span:"6"` often renders as a single slot.
  Workaround: call `ctrl:setBounds({...})` for every tile in `preset.onLoad`.

### Logger

`print(...)` is the only way to write to the Lua tab's log window. **The
logger is OFF by default** ("for performance reasons", per the docs) —
the user has to enable it in the Lua tab UI before any `print` or fatal-
error stack trace will appear. Firmware 4.1.4 added a fatal-error handler
that prints debug + stack trace to the logger; harvest those when
diagnosing silent failures.

`controller.uptime()` returns ms since boot — the only time API. Use it
for double-click detection between events, debouncing, etc.

## Firestore PATCH workflow (push to device without going through the UI)

This is the fast path to test changes on hardware. The full step-by-step
is in `HOWTO.md` section 2; the short version:

```bash
# 1. Extract the public Firebase API key from a fresh app.electra.one bundle
FIREBASE_API_KEY=$(for js in $(curl -s https://app.electra.one/presets/ | grep -oE '/_nuxt/[a-z0-9]+\.js' | sort -u); do
  curl -s "https://app.electra.one$js" | grep -oE 'AIza[0-9A-Za-z_-]{35}'
done | sort -u | head -1)

# 2. Auth (use $ELECTRA_PASSWORD env var, do NOT commit the password)
TOKEN=$(curl -s -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=$FIREBASE_API_KEY" \
  -H "Content-Type: application/json" \
  --data-raw "{\"email\":\"$ELECTRA_EMAIL\",\"password\":\"$ELECTRA_PASSWORD\",\"returnSecureToken\":true}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['idToken'])")

# 3. PATCH the preset (scratch preset id is in MEMORY.md / HOWTO.md)
curl -s -X PATCH \
  "https://firestore.googleapis.com/v1/projects/electra-one-716c4/databases/(default)/documents/projects/$PRESET_ID?updateMask.fieldPaths=project&updateMask.fieldPaths=name&updateMask.fieldPaths=schemaVersion&updateMask.fieldPaths=revision" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @payload.json
```

Then the user hard-refreshes `app.electra.one/preset/$PRESET_ID` (Ctrl+Shift+R
— the Nuxt cache is stubborn) and clicks **Upload**.

Bump the project's `revision` integer each push. Put a `WIDGET_REV` constant
at the top of the widget Lua so the on-screen header shows which version is
currently loaded — saves a round of "did the upload actually happen" questions.

## Conventions used in this repo

- Theme palette: cool slate neutrals (CANVAS / SURFACE / ELEVATED / BORDER /
  TEXT_DIM / TEXT) + warm amber-terracotta accent (ACCENT) and supporting
  POSITIVE (sage) / WARNING (vintage VU yellow) / ALERT (red) / INFO (steel
  blue). No rounded corners — match the real MK2 hardware look.
- Active state on a step / cell: `Theme.ELEVATED` background + `Theme.TEXT`
  outline + a 2-px `Theme.TEXT` top edge as the signature marker. This is
  consistent across step-seq-16, modern-adsr, eq-3band, etc.
- Header for a lane / sub-section: name in `Theme.TEXT_DIM` UPPERCASE on the
  left, hairline `Theme.line(..., Theme.BORDER)` underneath, live state /
  value readout on the right in the lane's accent colour.
- Mode pills: outline (NAV / default) vs filled-with-accent (EDIT / active).

If you're adding a new widget, copy the structure of an existing one
(`step-seq-16` is the canonical reference for grids; `modern-adsr` for
graph-based widgets) before improvising.

## Where to look when stuck

- **First reflex** — `rtfm_search "<term>"` on the project workspace. The
  official Electra One docs are mirrored locally at
  `.electra-docs/md/` (scraped from `docs.electra.one`, 79 pages) and
  indexed by RTFM. Faster and more complete than going through
  WebFetch (which often returns the SPA shell with no content).
- `widgets/<slug>/widget.lua` — example implementations, all rendered in
  the emulator. `widgets/note-list-16/` is the one device-validated
  custom-paint widget at firmware 4.1.4.
- `widgets/xt-envelopes/` — single custom tile, known to also work on
  device. Useful as a minimal reference for preset JSON structure.
- `docs.electra.one/developers/luaext.html` and the rest of the
  `developers/` section — official API. Mirror in `.electra-docs/md/`
  is grep-able and complete.
- `docs.electra.one/luacourse/` — MIDI & Lua crash course (chapters on
  dec/hex, MIDI messages, RPN/NRPN, SysEx, bit operations, Lua values /
  conditions / loops / tables / functions / modules / objects). Often
  the best place to learn the *idioms* the API expects, not just what
  exists. Mirror in `.electra-docs/md/luacourse/`.
- `forum.electra.one` — limits and gotchas not in the official docs.
  Key threads: #4172 (multi-value custom controls — partially
  implemented, web editor doesn't expose yet), #4116 / #4185 (encoder
  press not detectable), #4058 (silent Lua compilation failure at
  ~265 KB on firmware 4.1.2), #4259 (firmware 4.1.4 release notes:
  added fatal-error Lua handler, fixed RGB888→RGB565 translation,
  fixed memory leak in `controls.get()`).

## Pushing presets to a real device via SysEx (Windows USB-MIDI specifics)

These notes apply when the MCP `push_to_device` tool runs against a physical
Electra One MK2 over USB on a Windows host. Learned the hard way (forum #592
corroboration + on-hardware testing 2026-05-25/26).

**The simple `01 01` upload command is fragile above ~5 KB on Windows.** The
USB-MIDI 1.0 class driver fragments large `midiOutLongMsg` calls at the USB
packet layer, and the device parses only the first fragment as a truncated
preset. Windows updates KB5077181 / KB5074105 (late 2025) made this worse.
Don't try to brute-force it by splitting `midiOutLongMsg` calls of the same
SysEx — each call becomes its own SysEx frame on the wire (own start/end
markers) and the device sees N small malformed presets, not one big one.

**Use the File Transfer API for any preset > ~5 KB.** Each step is its own
small complete SysEx so the driver doesn't fragment:
1. Open cache:    `F0 00 21 45 01 2D F7`
2. Register file: `F0 00 21 45 01 2E <fileId> <s0> <s1> <s2> <s3> F7` — size split as four 7-bit bytes, LE
3. Chunks (≤ 1-2 KB ASCII each): `F0 00 21 45 01 2F <fileId> <data> F7`
4. Commit: `F0 00 21 45 04 2D {"files":[{"id":1,"location":"slots","type":"preset","bankNumber":B,"slot":S,"md5":"<32-hex>"}]} F7` — MD5 over the **decoded** payload (raw JSON), not the SysEx-wrapped bytes.
5. Include a **Transaction ID** in every step (firmware ≥ 4.0): insert `0x00 <txid-lsb> <txid-msb>` right after the manufacturer id. Without it, ACKs can't be correlated.

**ACK format**: `F0 00 21 45 7E 01 <txid-lsb> <txid-msb> F7` (success); `7E 00 …` for NACK. Check after every command; bail on NACK with a useful error.

**CTRL port on Windows for this hardware**: `MIDIOUT3 (Electra Controller)` ↔ `MIDIIN3 (Electra Controller)`. The bare-named "Electra Controller" is port 1, MIDIOUT2/IN2 is port 2.

**Listening for ACKs (winmm + ctypes) — two non-obvious traps**:
- **Polling `MHDR_DONE` races the driver write-back** → you read `F0` followed by zeros. Use `CALLBACK_FUNCTION` with `WINFUNCTYPE` so the driver hands you a complete buffer.
- **`hdr.lpData` as `c_char_p` truncates at the first NUL byte** (Electra SysEx contains `0x00` right after `F0`). Don't read via `lpData`. Keep the Python-side `ctypes.create_string_buffer` allocations in a list, find which one this header refers to via `ctypes.addressof(headers[i]) == ctypes.addressof(hdr)`, and read `buffers[i].raw[:n]`.

**Long-term path** (when Win 10 support can be dropped): port to **Windows MIDI Services 1.0** via `winsdk` / `pywinrt`. Shipped to Win 11 in Feb 2026; replaces `winmm.dll` and fixes the USB-MIDI 1.0 fragmentation issue.

**Reference impl + ongoing state**: `server/win_bridge.py` in [electra-one-mcp](https://github.com/roomi-fields/electra-one-mcp). See `HOWTO.md` section "Pushing presets via SysEx" for the full play-by-play and forum thread #592.

**Major root-cause discovery (2026-05-26): preset schema mismatch.**

The widget files in `electraone-widgets/widgets/*/demo.preset.json` use a
"new" schema that the **device firmware does not parse via direct SysEx**:
`schemaVersion`, `tiles`, `targetDevice`, `firstPageId`, `categoryId` per
tile — none of these appear in [presetformat.html](https://docs.electra.one/developers/presetformat.html). The documented schema uses `version` (numeric), `pages`,
`devices`, `overlays`, `groups`, `controls` (no `tiles`).

The web editor at `app.electra.one` converts our "tiles" schema to the
documented "controls" schema before sending the SysEx. Pushing the raw
"tiles"-format JSON via `01 01` (or via the FT API with `type:"preset"`)
**ACKs successfully at the transport level but the on-device preset parser
silently rejects the structure** — `Get Active Preset` (`02 01`) afterwards
returns 0 bytes, and the screen shows "no name - page 1" or stays empty.

This explains every "silent rollback" we've observed for big presets — it
was never a size/MD5/chunking issue, it was always a schema mismatch.

To push to the device without going through the website, you need to either:
- Convert tiles → controls/groups before upload (and route the inlined `lua`
  field to a separate `01 0C` upload, per docs)
- Or sniff what app.electra.one actually sends (`navigator.requestMIDIAccess`
  → wrap `MIDIOutput.send`) and replicate that exact byte sequence

The MCP `push_to_device` in v0.x assumes the documented "controls" schema.
For "tiles"-schema presets, you must convert first or upload via the web app.

**Empirical limits confirmed on firmware 4.1.4 (2026-05-26)**:
- < ~5 KB via simple `01 01` upload: works, displays immediately
- < ~6 KB via FT API: commit succeeds, file persists, displays after reload trigger
- ≥ ~25 KB via FT API: all chunks ACK'd, commit ACK'd, `7E 05` + `7E 02` events fire, **but `Get Active Preset` (`02 01`) afterwards returns 0 bytes**. The file does not actually land on disk — silent MD5-rollback most likely. Open issue.
- **`Reload Preset Slot` (`08 08`) returns NACK** in 4.1.4 despite docs listing it as no-args. Either docs are wrong about the args, or the command got renamed.
- **No SysEx query for "which bank/slot is on screen"**. Maintain client state by passively listening for unsolicited `7E 02 bank slot` (preset switch) and `7E 08 bank` (bank switch) events on the CTRL port.

## Reflex when something breaks silently on device

- Widget renders as a blue bar / default fader → paint callback raised an
  error. Enable the logger, hard-refresh, re-upload, read the stack trace.
- Widget shows `rev X` (your WIDGET_REV constant) but interactions do
  nothing → callbacks attached but firmware isn't dispatching events to
  them. Most likely cause: tile was repositioned via `setBounds` away
  from its `slotId`, or the tile only has one `values[]` entry so only one
  pot is mapped.
- "Preset is empty" in the editor after PATCH → project JSON is
  structurally invalid. Revert by patching with a known-good project (XT
  envelopes' JSON works) then re-clone with the fields you need.
- Upload button missing → auth/session expired (1h TTL on the Firebase
  ID token). Refresh the token.
