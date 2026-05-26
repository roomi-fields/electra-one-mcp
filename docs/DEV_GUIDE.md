# Electra One Dev Guide

User-facing reference for developing custom widgets and presets for the Electra One MK2 / Mini using this plugin. Pairs with the `dev-electra-one` skill and the 20 MCP tools exposed by `server.py`.

## 1. The pieces

| Layer | What it is | Where to look |
|---|---|---|
| Lua extension API | Functions you can call from a preset's `main.lua` (`graphics`, `controls`, `parameterMap`, `controller`, `events`, `timer`, `midi`, `preset`, `device`) | `docs/md/developers/luaext.md` + `docs/structured/api.json` (180 symbols) |
| Preset JSON format | The `controls`-schema the firmware parses (`version` + `pages` + `devices` + `overlays` + `groups` + `controls`) | `docs/md/developers/presetformat.md` |
| SysEx protocol | 62 commands the host can send + events the device emits | `docs/structured/sysex_commands.json` + `docs/md/developers/midiimplementation.md` + `docs/md/developers/filetransfer.md` |
| Device-side gotchas | Quirks learned the hard way (graphics primitives need integer coords, etc.) | `docs/structured/gotchas.json` (17 entries) |
| Reverse-engineered web editor logic | `app.electra.one` does a `tiles`-schema → `controls`-schema conversion before SysEx upload; we port that in `preset_converter.py` | `docs/RE_NOTES.md` |
| Forum staff confirmations | Quotes from Martin / kris explaining undocumented behaviour (file transfer limits, fragmentation, etc.) | `docs/FORUM_REFERENCES.md` |

## 2. The widget development loop

The typical iteration with this plugin's MCP tools:

```
bundle_widget       → assemble theme + primitives + widget.lua into one Lua blob
validate_preset     → catch schema errors offline before push
push_to_device      → upload preset.json + main.lua; device reloads automatically
device_state /      → inspect what's running, capture pot/button events
  execute_lua       → run a Lua snippet for live introspection (REPL)
(edit) → loop
pull_preset         → when device state has drifted ahead of repo, fetch it back
```

### Step-by-step

**1. Scaffold from an existing widget.** Copy `widgets/note-list-16/` to `widgets/my-widget/`. Edit `widget.lua` for your paint logic. Tile schema in `demo.preset.json` mostly stays the same — change `id`, `name`, the tile's `name`, and any custom values you reference.

**2. Bundle locally.**

```bash
node scripts/bundle-preset.js my-widget
```

This concatenates `lib/theme.lua` + every required `lib/primitives/*.lua` + your `widget.lua` and writes the result into `demo.preset.json`'s `lua` field.

**3. Push to the device.**

From inside Claude Code:

```
push_to_device(preset_path="widgets/my-widget/demo.preset.json", bank=0, slot=0)
```

Or directly via the CLI bridge:

```bash
python3 server/win_bridge.py upload-preset \
  --preset widgets/my-widget/demo.preset.json \
  --port "MIDIOUT3 (Electra Controller)" \
  --bank 0 --slot 0 --mode simple
```

Either way, the pipeline is:

1. `09 08 bank slot` — switch to target slot
2. `01 01 <preset.json>` — upload preset (converted from tiles schema if needed)
3. `01 0C <main.lua>` — upload Lua
4. Slot-flip reload — `09 08 bank (slot XOR 1)` then back — forces cold re-init (`08 08` Reload Preset Slot NACKs on firmware 4.1.4)

**4. Inspect runtime state.**

Lua REPL — runs without saving:

```
execute_lua("print(parameterMap.get(99))")
execute_lua("print(controls.get(1):getName())")
execute_lua("for _, c in ipairs(controls.getList()) do print(c:getId(), c:getName()) end")
```

Output captured via the device's `7F 00` log SysEx.

State snapshot:

```
device_state(seconds=2)
```

Returns `state.{bank, slot, page, control_set, snapshot_bank}` last-known values plus an event log + log lines. Subscribe to additional events first if you want pot touches / button presses:

```
subscribe_events(["pots", "touch", "button"])
device_state(seconds=5)   # touch the encoder, button, screen during these 5s
```

**5. Iterate.** Edit `widget.lua` → re-bundle → re-push. The slot-flip baked into `push_to_device` means the device re-loads cleanly each time; no physical reboot needed.

If you get a runtime Lua error, `device_state` returns it in the `log` field. Most common cause: a missing `math.floor()` on a `graphics.drawLine` / `graphics.print` coordinate (the firmware refuses non-integer args).

**6. Pull back what's on the device.**

When you've edited a preset on the device (or another tool wrote it) and want to capture it into the repo:

```
pull_preset(bank=0, slot=0, out_path="widgets/my-widget/demo.preset.json")
```

This combines `02 01 Get Active Preset` + `02 0C Get Lua` and reverse-converts the `controls` schema back to the repo's `tiles` schema. `git diff` will show you what changed.

## 3. MCP tools (20 total)

| Need | MCP tool |
|---|---|
| Push a widget to a slot | `push_to_device(preset_path, bank, slot)` |
| Inspect runtime | `execute_lua(source)` — REPL with `print()` capture |
| What's on screen | `device_state(seconds)` |
| Read the Lua on device | `get_lua_source(bank, slot)` |
| Capture device → repo | `pull_preset(bank, slot, out_path)` |
| Subscribe to more events | `subscribe_events(["pots", "touch", "button", ...])` |
| Get firmware/serial info | `device_status()` |
| Tail device log lines | `get_device_logs(seconds)` |
| Clear a slot | `clear_preset_slot(bank, slot)` |
| Upload side files | `upload_devices_overrides`, `upload_persisted_data`, `upload_performance` |
| Upload reusable Lua module | `upload_lua_module(path, namespace, name)` |
| Bundle theme + primitives + widget | `bundle_widget(theme, primitives, widget)` |
| Validate preset before push | `validate_preset(json)` |
| Render via emulator | `screenshot_widget(slug)` |
| Search official docs | `search_docs(query)` |
| Lua API symbol lookup | `get_api(symbol)` |
| Enum constants lookup | `list_constants(category)` |
| Any SysEx command lookup | `get_sysex_command(query)` — 62 cataloged |

## 4. The MK2 vs Mini distinction

The plugin ships layout math for both devices. `preset_converter.py` selects via `target_device` in the project JSON (defaults to `mk2` if absent):

| Device | Grid | Slots per page | Pages | Display |
|---|---|---|---|---|
| MK2 | 6 cols × 12 rows | 72 | 12 | 1024 × 565 |
| Mini | 4 cols × 6 rows | 24 | 8 | 800 × 440 |

Each "row" alternates label (height ~30) and control (height ~60); slot indexing is `(page-1) * slotsPerPage + (y * slotsPerRow + x)`. The full constants are in `preset_converter.py` (`_MK2Layout`, `_MiniLayout`) and in `docs/RE_NOTES.md`.

## 5. Where things go wrong (and how to recover)

- **Empty screen / "no name - page 1" after push** → the tiles JSON went straight through without conversion. Verify the file has `schemaVersion: 2` + `tiles[]` at top level; `push_to_device` auto-detects and converts. If the bundle is missing the `lua` field, only the title strip renders.

- **Lua errors in a loop** (visible via `device_state` → `log`) → usually a non-integer coordinate. `graphics.drawLine`, `graphics.print`, `graphics.fillRect` all require integer args. `math.floor()` everywhere defensive.

- **`Reload Preset Slot` (`08 08`) NACKs** → known on firmware 4.1.4; the plugin uses the slot-flip workaround automatically. Don't try to call `08 08` directly.

- **`File Transfer API` commit NACKs** → known limitation when pushing a `type:"preset"` via FT (the docs scope FT to firmware / lua modules / multi-file deploys). `push_to_device` uses the simple `01 01` + `01 0C` path instead.

- **`Get Active Preset` returns 0 bytes** → either the slot is genuinely empty, OR (in our test history) the previous upload's MD5 mismatched silently. Reboot the device, re-push.

- **Device stops responding after rapid SysEx** → the device's queue isn't backpressured; rapid-fire commands can wedge it. Wait 1–2 s between heavy uploads, or run `device_status()` between to verify liveness.

## 6. Further reading

- `docs/RE_NOTES.md` — reverse-engineering notes: bundle offsets, function ports, layout math, why the converter exists
- `docs/FORUM_REFERENCES.md` — staff quotes and links for the undocumented behaviour we encountered
- `docs/md/developers/luaext.md` — official Lua extension API
- `docs/md/developers/midiimplementation.md` — official SysEx protocol
- `docs/md/developers/filetransfer.md` — official File Transfer API
- `skills/dev-electra-one/SKILL.md` — the dev-electra-one skill (what the agent loads automatically)
