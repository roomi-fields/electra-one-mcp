# electra-one-mcp

A Claude Code plugin for developing custom widgets and presets on the [Electra One](https://electra.one) MK2 / Mini MIDI controller. Push presets directly to hardware over USB, run a Lua REPL on the device for live inspection, capture knob/button events, pull the preset back into your repo, all without leaving Claude.

What you get when you install:

- **20 MCP tools** that wrap every common EL1 dev operation — push, inspect, REPL, pull, validate, search docs.
- **`dev-electra-one` skill** auto-loaded as soon as you touch an Electra One file. Primes Claude with the device-side gotchas + the cheat sheet of all 20 tools.
- **The full official docs mirrored locally** (79 pages from `docs.electra.one`) + a machine-readable distillation (180 Lua API symbols, 15 enum categories, 17 device-side gotchas, 62 SysEx commands, layout math for MK2 + Mini).
- **A tiles ↔ controls schema converter** ported byte-identical from `app.electra.one`'s JS bundle — push our repo-format widget JSONs straight to hardware without round-tripping through the web editor.
- **Three written guides**: a user-facing development walkthrough, reverse-engineering notes for the bundle ports, and a forum-references file with direct quotes from Electra staff explaining undocumented behaviour.

## Why this exists

The Electra One Lua extension is powerful but its documentation is split across `docs.electra.one`, the forum (where most of the practical workarounds live as staff replies in support threads), and tribal knowledge. We lost hours rediscovering things like:

- `graphics.drawText` doesn't exist on device — use `graphics.print(x, y, text, w, align)`.
- The `inputs` array in the preset JSON is silently ignored for `type:"custom"` tiles (firmware limit, forum #4172).
- `Reload Preset Slot` (`08 08`) NACKs on firmware 4.1.4 despite the docs listing it — you need a slot-flip workaround.
- The widget JSONs in this repo are in a `tiles` schema the device firmware *doesn't parse* — the web editor converts to a `controls` schema before SysEx upload.

This plugin packages those findings plus the official docs so future sessions don't repay the discovery tax. Every empirical claim ships with a source citation (forum thread, bundle offset, or hardware verification note).

## Install

Inside a Claude Code session:

```
/plugin marketplace add roomi-fields/claude-plugins
/plugin install electra-one@roomi-fields
```

Then restart Claude Code so the MCP server registers. The skill activates automatically as soon as you touch any Electra One Lua / preset code; the MCP tools become available immediately.

## Prerequisites

- **Python ≥ 3.10** with the MCP SDK:

  ```bash
  pip install -r requirements.txt
  # or, on PEP 668 systems:
  pip install --break-system-packages -r requirements.txt
  ```

  `python-rtmidi` and `mido` are pulled in for the native Linux/macOS path. On Windows / WSL the plugin uses a direct `winmm.dll` bridge instead, so rtmidi is optional there.

- **An Electra One MK2 (or Mini)** connected over USB MIDI to your host. The plugin auto-targets the **CTRL port** (`MIDIOUT3 / MIDIIN3 (Electra Controller)` on Windows).

- **OS support**:
  - **macOS** — works out of the box via CoreMIDI + mido.
  - **Linux native** — works out of the box via ALSA + mido.
  - **Windows native** — works via the built-in `winmm.dll` (no driver install).
  - **WSL2** — works *transparently* via a Python helper that runs on the Windows host (`server/win_bridge.py`). The MCP shells out to `powershell.exe` to talk to `winmm.dll` on the Windows side. **No `usbipd-win` required**, no `/dev/snd/seq` setup, no extra drivers.

- **Node ≥ 18** — optional, only for `screenshot_widget` (headless emulator capture).

## Quick start

After install, push any widget JSON to the device:

```
push_to_device(preset_path="widgets/step-seq-16/demo.preset.json", bank=0, slot=0)
```

The plugin auto-detects the schema (tiles vs controls), runs the converter if needed, splits the inlined Lua into a separate upload, sends the 5-step pipeline (switch slot → upload preset → upload lua → slot-flip reload), and verifies every ACK along the way.

To inspect what's happening on the device:

```
execute_lua("print(parameterMap.get(99))")
device_state(seconds=2)
```

The first runs a Lua snippet on the device and captures `print()` output (REPL). The second listens for unsolicited events and returns the last-known bank/slot/page + recent activity.

To pull a preset off the device back into the repo:

```
pull_preset(bank=0, slot=0, out_path="widgets/my-widget/demo.preset.json")
```

This downloads preset.json + main.lua, runs the reverse converter (`controls` → `tiles`), and writes a JSON ready to `git diff`.

See `docs/DEV_GUIDE.md` for the full iteration loop and `docs/structured/sysex_commands.json` for every SysEx command available.

## The 20 MCP tools

Grouped by the dev need they cover:

**Push** (move JSON / Lua onto the device)

| Tool | What it does |
|---|---|
| `push_to_device(preset_path, bank, slot)` | The headline tool. Auto-converts tiles→controls, splits Lua, runs the 5-step pipeline with ACK verification. |
| `upload_devices_overrides(path)` | Upload devices.json (port/channel remap) — `01 0F` SysEx. |
| `upload_persisted_data(path)` | Upload data.json (Lua `persist()` table) — `01 12`. |
| `upload_performance(path)` | Upload performance.json (macro view) — `01 11`. |
| `upload_lua_module(path, namespace, name)` | Upload a reusable Lua module to `/ctrlv2/lua/<ns>/<name>.lua` via the File Transfer API. |
| `clear_preset_slot(bank, slot)` | Wipe all files in a slot — `05 08`. |

**Read** (inspect what's running)

| Tool | What it does |
|---|---|
| `execute_lua(source)` | Run a Lua snippet on the device WITHOUT saving it (REPL). Captures `print()` output. |
| `device_state(seconds)` | Passive listen — returns last-known bank/slot/page/control-set + recent events + log lines. |
| `get_lua_source(bank, slot)` | Download main.lua from a slot. |
| `pull_preset(bank, slot, out_path)` | Combined: preset + lua + reverse-convert to tiles schema, write to disk. |
| `subscribe_events(flags)` | Enable richer event delivery — pots, touch, button, window, etc. |
| `device_status()` | Query firmware version + serial + model. |
| `get_device_logs(seconds)` | Tail the device's log channel (legacy; `device_state` returns logs too). |

**Compute** (offline / repo-side)

| Tool | What it does |
|---|---|
| `bundle_widget(theme, primitives, widget)` | Concatenate theme + primitives + widget into one Lua blob. |
| `validate_preset(json)` | Schema + known-bad-pattern check (drawText calls, IIFE wraps, etc.). |
| `screenshot_widget(slug)` | Headless Playwright capture from the emulator. |

**Doc lookup** (no device needed)

| Tool | What it does |
|---|---|
| `search_docs(query, kind)` | Full-text search of the mirrored 79-page documentation. |
| `get_api(symbol)` | Signature + params + example for a Lua API symbol (`graphics.print`, `parameterMap.onChange`, …). |
| `list_constants(category)` | Enum constants by category — touch events, MIDI messages, alignments, models. |
| `get_sysex_command(query)` | Look up any of the 62 catalogued SysEx commands by name or keyword. |

## What's in the box

```
electra-one-mcp/
├── .claude-plugin/plugin.json       # Claude Code manifest
├── skills/
│   └── dev-electra-one/SKILL.md     # always-on Claude skill (auto-loads)
├── server/
│   ├── server.py                    # 20 MCP tools
│   ├── win_bridge.py                # winmm/ctypes bridge + CLI (Linux/macOS/Windows/WSL)
│   ├── preset_converter.py          # tiles ↔ controls schema converter (ported from app.electra.one)
│   └── __init__.py / __main__.py
├── docs/
│   ├── DEV_GUIDE.md                 # user-facing widget-development guide ★ READ FIRST
│   ├── RE_NOTES.md                  # reverse-engineering notes (bundle offsets, function ports)
│   ├── FORUM_REFERENCES.md          # staff quotes for undocumented behaviour
│   ├── md/                          # 79-page mirror of docs.electra.one
│   └── structured/
│       ├── api.json                 # 180 Lua API symbols
│       ├── constants.json           # 15 enum categories
│       ├── gotchas.json             # 17 empirically-confirmed device-side quirks
│       ├── sysex_commands.json      # 62 SysEx commands + events
│       └── layout_constants.json    # MK2 + Mini layout math
├── scripts/
│   ├── refresh-docs.sh              # re-scrape docs.electra.one
│   └── build-structured.py          # regenerate docs/structured/*.json
└── examples/
    ├── push-and-watch.md
    └── overlay-recipe.md
```

## Recommended reading order

1. **`docs/DEV_GUIDE.md`** — the iteration loop, all 20 MCP tools, troubleshooting playbook.
2. **`docs/md/developers/luaext.md`** — official Lua extension API.
3. **`docs/md/developers/midiimplementation.md`** + **`docs/md/developers/filetransfer.md`** — official SysEx protocol.
4. **`docs/structured/sysex_commands.json`** — queryable catalog of 62 SysEx commands.
5. **`docs/RE_NOTES.md`** — what we reverse-engineered from `app.electra.one`. Read this before modifying `preset_converter.py`.
6. **`docs/FORUM_REFERENCES.md`** — staff quotes for the undocumented behaviour.

## Known limitations

- **File Transfer API + `type:"preset"`** silently rolls back on commit for firmware 4.1.4. We default to the simple `01 01` + `01 0C` path instead (what the web editor uses). The FT API is exposed in `mode="ft"` for completeness + for Lua module uploads where it's the only path.
- **`08 08 Reload Preset Slot`** NACKs on firmware 4.1.4. The plugin uses a slot-flip workaround automatically.
- **No SysEx query for "current bank/slot"** — the device only emits unsolicited `7E 02 bank slot` events on user navigation. `device_state` tracks these client-side.
- **Windows USB-MIDI fragmentation** above ~5 KB single-SysEx — known since the late-2025 Windows updates KB5077181 / KB5074105. We work around by keeping `01 01` payloads small (< 1 KB after extracting Lua) and chunk-aware where needed. Long-term: port to Windows MIDI Services 1.0 (Win 11 24H2+).
- **MIDI capture / playback** + **snapshot write/move/swap** SysEx commands are documented in `sysex_commands.json` but not yet wrapped as MCP tools. Use `get_sysex_command(...)` to look up bytes if you need them.

## Acknowledgements

- Electra One team (Martin Pavlas, Thomas Moravansky) for the hardware and the publicly-documented Lua API.
- Forum threads #592, #410, #2370, #2022, #807, #3590, #4172, #4116, #4185, #4058, #4259 for the staff quotes and confirmations that made this plugin possible — see `docs/FORUM_REFERENCES.md` for direct quotes and links.
- `xot/ElectraOne` (Codeberg), `johnnyclem/electra-one`, `elliotwoods/simularca-electra-one-plugin` as reference open-source consumers of the SysEx protocol.

## License

MIT.
