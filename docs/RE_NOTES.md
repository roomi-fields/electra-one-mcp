# Reverse-engineering notes — app.electra.one bundle

This document captures the bundle offsets, function ports, and layout math we extracted from `app.electra.one`'s JavaScript bundle. The motivation: the device firmware parses a different preset schema than the one this repo's widgets use, and the conversion logic was only available inside the web editor's minified JS. We ported it byte-identical (verified against a live MIDI sniff).

If `app.electra.one` ships a new bundle and the offsets shift, this is the file to update. The numbers below are for bundle hash `0c61af0.js` (the main bundle as of 2026-05-26).

## How the bundle was acquired

The full editor bundle (10 main `.js` files + 404 lazy chunks, ~28 MB total) was mirrored locally during the 2026-05-26 session. To refresh:

```bash
mkdir -p /tmp/elatra-bundle/raw /tmp/elatra-bundle/lazy
for js in $(curl -s https://app.electra.one/preset/<any-id> | grep -oE '/_nuxt/[a-z0-9]+\.js' | sort -u); do
  curl -s "https://app.electra.one$js" -o "/tmp/elatra-bundle/raw/$(basename $js)"
done
# Then extract all 7-char hex chunk IDs from those files and download each:
grep -hoE '"[a-f0-9]{7}"' /tmp/elatra-bundle/raw/*.js | tr -d '"' | sort -u | \
while read id; do
  curl -s "https://app.electra.one/_nuxt/${id}.js" -o "/tmp/elatra-bundle/lazy/${id}.js"
done
```

This gives you the full editor source for grep/inspection.

## The tiles → controls conversion

Source: `0c61af0.js`, function `projectToPreset` around offset **127000–131000** in the minified bundle.

What it does:
- Reads our `tiles`-schema input (the format saved in Firestore by the web editor and used in this repo's `widgets/*/demo.preset.json` files).
- Outputs the `controls`-schema the device firmware actually parses (per `docs.electra.one/developers/presetformat.html`).
- Generates per-tile bounds + pageId + controlSetId + inputs from the tile's `slotId` via layout helpers.

The Python port lives in `server/preset_converter.py::project_to_preset`. Verified byte-identical to the web editor's output: for `slotId=1, span=undef, vspan=undef` it produces `bounds=[181, 6, 158, 16]` + `potId=8`, exactly matching what a live MIDI sniff captured.

Key transformations:

| Source (tiles) | Output (controls) | Notes |
|---|---|---|
| `schemaVersion: 2` | `version: 2` | numeric, the device parser ignores `schemaVersion` |
| `id: "<projectId>"` | `projectId: "<projectId>"` | the device parser ignores top-level `id` |
| `tiles[]` | `controls[]` | one tile → one control |
| `tile.reference` (numeric) | `control.id` | the tile's display index becomes the control id |
| `tile.slotId` | `control.bounds`, `pageId`, `controlSetId`, `inputs[0].potId` | computed via the layout helpers (see below) |
| `tile.name` | `control.name` (uppercased) | per the bundle's `String(...).toUpperCase()` |
| `tile.values[]` | `control.values[]` | passed through; deviceId propagated into each `message` |
| `tile.values[i].message.type === "relative"` | `type:"cc7", relative:true` | the bundle does this remapping |
| `groups[]` | `controls[]` with `type:"label"` | groups are flattened into the controls array |
| `targetDevice`, `categories`, `firstPageId`, `lua` | dropped | not part of the controls schema; lua goes to a separate `01 0C` upload |
| (added) | `overlays: []`, `groups: []` | empty arrays required by the schema |

## MK2 layout math

Source: `0c61af0.js` around offset **159200–160500**. The bundle defines a `_MK2Layout`-like object with these methods:

```js
slotIdToPageId: (e) => Math.floor(e / 72) + 1
slotIdToX:      (e) => e % 6
slotIdToY:      (e) => {
  const page = Math.floor(e / 72) + 1;
  const rel  = e - 72 * (page - 1);
  return Math.floor(rel / 6);
}
slotIdToBounds: (e, span=1, vspan=0) => {
  const x = e % 6;
  const y = Math.floor(e / 6) % 12;
  const l = Math.floor(y / 2);
  let d = 20 + 167 * x;
  let w, h;
  if (y % 2 === 0) {                       // group / label row
    w = 146 * span + 21 * (span - 1) + 12;
    h = vspan > 0 ? 90 * vspan - 9 : 16;
    d -= 6;
  } else {                                 // control row
    w = 146;
    h = 56;
  }
  const py = 6 + 22 * ((y / 2 | 0) + (y % 2)) + 68 * l;
  return [d, py, w, h];
}
slotIdToPot: (e) => {
  const x = e % 6;
  return (Math.floor(e / 6) % 4 === 1) ? x + 1 : x + 7;
}
slotIdToSet: (e) => (Math.floor(e / 6) % 12) / 4 | 0 + 1
boundsToSlotId: (bounds, pageId) => {
  const col = Math.floor(bounds[0] / 170);
  return 72 * (pageId - 1)
       + 6 * (2 * Math.floor((bounds[1] - 6) / 90)
              + ((bounds[1] - 6) % 90 === 0 ? 0 : 1))
       + col;
}
```

Layout constants (offset ~129500):

```
mk2:  numPages: 12, slotsPerPage: 72, slotsPerRow: 6, rowsOnPage: 6,
      slotsInSection: 24, controlsInSection: 12,
      displayWidth: 1024, displayHeight: 565,
      controlWidth: 168, controlHeight: 60,
      groupWidth: 168, groupHeight: 30, groupSpanHeight: 97,
      maxVspan: 6
      rowY: [0, 30, 90, 120, 180, 210, 270, 300, 360, 390, 450, 480]
mini: numPages: 8, slotsPerPage: 24, slotsPerRow: 4, rowsOnPage: 3,
      slotsInSection: 24, controlsInSection: 12,
      displayWidth: 800, displayHeight: 440,
      controlWidth: 194, controlHeight: 125,
      groupWidth: 195, groupHeight: 30, groupSpanHeight: 175,
      maxVspan: 2
      rowY: [0, 30, 170, 200, 340, 370]
```

Each "row" in the 6×12 / 4×6 grid alternates label (even y) and control (odd y), so a "section" is one label + one control row + adjacent next section.

The Python ports of these helpers are in `server/preset_converter.py::_MK2Layout` and `_MiniLayout`. They produce identical output to the bundle's JS for every slotId we tested.

## The reverse direction: controls → tiles

Source: `0c61af0.js`, function `presetToProject` around offset **134272**.

It's the inverse of `projectToPreset`:
- `control.id → tile.reference; tile.id = freshly-generated string`
- `bounds → slotId` via `boundsToSlotId(bounds, pageId)` (the inverse formula above)
- `groups` become `type:"label"` tiles with `span = floor(bounds[2] / 146)`
- Devices are filtered to only those referenced by some control; pages to those holding some control
- Overlays are denormalised: if a value has `overlayId`, the overlay items get inlined as `value.textValues`

The Python port: `server/preset_converter.py::preset_to_project`. Round-trip verified (`project_to_preset(preset_to_project(p))` preserves the slotId of the source).

## The upload pipeline

Source: `0c61af0.js`, action `uploadProject` around offset **63186**.

The web editor's flow when the user clicks "Send to Electra":

```js
uploadProject: function(_, t) {
  const r = t.project,
        n = t.targetDevice;
  const layout = getters["project/layoutByTargetDevice"](n);
  const converted = projectToPreset(r, layout);
  dispatch("uploadPreset", converted);     // 01 01 SysEx
  dispatch("uploadLua", r.lua);            // 01 0C SysEx
  commit("setProject", r);
}
```

Two key takeaways:

1. **Lua is uploaded separately, not inlined in preset.json.** Even though our tiles JSON has a `lua` field at top level, the web editor strips it from the preset before sending and uploads it as a separate `01 0C Upload Lua Script` SysEx. Our `preset_converter.split_preset_for_upload` does the same.

2. **The web editor uses the simple `01 01` + `01 0C` path, not the File Transfer cache API.** This is why our `push_to_device` defaults to `mode="simple"` — the cache API is documented for firmware / lua modules / multi-file deploys, and the commit silently rolls back for preset content (forum #592 corroboration).

## CTRL port routing

The bundle picks the CTRL port by name pattern. From `0c61af0.js` around offset 63500:

```js
t.name.match(/(Electra.*(CTRL|Port 3|MIDI 3))|(MIDIIN3).*Electra|(MIDIOUT3).*Electra|^Port 3/i)
```

So on Windows the CTRL port surfaces as `MIDIOUT3 (Electra Controller)` / `MIDIIN3 (Electra Controller)`. Our `push_to_device` defaults to those names.

## What we did NOT port

These exist in the bundle but the plugin doesn't wrap them yet (low priority based on dev ergonomics):

- **Snapshot edit/move/swap** — `04 06 Update Snapshot`, `06 06 Swap Snapshots` exist but we only expose `02 03 Get Snapshot` + `02 05 Get Snapshot List` via `get_sysex_command`.
- **MIDI capture / playback** — `02 30 Get Capture`, `02 31 Get Capture List`, etc. (niche feature)
- **Connection state machine** — the editor tracks USB connect/disconnect events and re-fetches preset list after each `7E 05 / 7E 31` notification. The MCP relies on the caller to re-query.
- **Per-control runtime overrides** — `14 0E Override Value Text` exists in the catalog but not wrapped.
- **Reverse converter UUID generation** — the bundle uses `uuid.v4()`; our Python port uses `f"control-{reference}"` for determinism (diff-friendly).

To extend the MCP with one of these, look up the SysEx bytes via `get_sysex_command("<query>")`, then add a thin helper to `win_bridge.py` mirroring the existing pattern (`_send_simple_cmd(out_port, op, resource, payload)`).

## Verification: how to know the converter is still correct

Sometimes app.electra.one updates the bundle. To check that our converter still produces byte-identical output:

1. Open `app.electra.one`, log in, open any preset.
2. Open DevTools → Console. Paste:

   ```js
   (() => {
     const orig = console.log;
     window.__captured = [];
     console.log = function(...args) {
       for (let i = 0; i < args.length; i++) {
         if (typeof args[i] === 'string' && /UPLOAD (preset|project|lua)/i.test(args[i])) {
           for (let j = i + 1; j < args.length; j++) {
             window.__captured.push({ tag: args[i], at: Date.now(), value: args[j] });
           }
         }
       }
       return orig.apply(console, args);
     };
     console.log('[capture hook installed] — now click Send to Electra');
   })();
   ```

3. Click "Send to Electra" in the editor.
4. `copy(JSON.stringify(__captured, null, 2))` and inspect what the editor logged for `UPLOAD preset:` — that's what it sent over WebMIDI.
5. Run the same source preset through our `preset_converter.project_to_preset()` and `diff` the two outputs.

If they diverge, the bundle's `projectToPreset` has been updated; refresh this doc and the converter accordingly.
