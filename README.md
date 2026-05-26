# electra-one-mcp

An MCP server + Claude Code plugin for developing custom widgets and presets on the [Electra One](https://electra.one) MK2 / Mini MIDI controller, with three integrated parts:

1. **Skill `dev-electra-one`** — primes Claude with the verified Lua API + the device-side gotchas learned the hard way (graphics.print signature, integer coords, 1-pot-per-custom-tile firmware limit, IIFE-free bundling, etc.).
2. **MCP server `electra-one`** — exposes nine tools so Claude can push presets, capture logs, search docs, validate, and bundle, all without you clicking around in `app.electra.one`.
3. **Indexed docs** — the full official documentation mirror (79 pages, including the MIDI & Lua crash course) plus a machine-readable distillation (`docs/structured/api.json`, `constants.json`, `gotchas.json`).

## Why this exists

The Electra One Lua extension is powerful but its documentation is split across `docs.electra.one`, the forum, and tribal knowledge. We've lost hours rediscovering things like "graphics.drawText doesn't exist on device, use graphics.print" or "the `inputs` array in the preset JSON is silently ignored for `type:"custom"` tiles". This plugin packages those findings + the official docs so future sessions don't repay the discovery tax.

## Install

Inside a Claude Code session:

```
/plugin marketplace add roomi-fields/claude-plugins
/plugin install electra-one@roomi-fields
```

Then restart Claude Code so the MCP server is registered. The skill
activates automatically as soon as you touch any Electra One Lua / preset
code; the MCP tools become available under the `electra-one` namespace.

Plug your Electra One in via USB before using the push / log / status
tools, and make sure `sendmidi` is on your PATH (see "Prerequisites").

## Prerequisites

- **Python ≥ 3.10** with the MCP SDK and MIDI deps:

  ```bash
  pip install -r requirements.txt
  # or:
  pip install mcp mido python-rtmidi
  ```

  On Debian/Ubuntu hosts that enforce PEP 668, use a venv or
  `pip install --break-system-packages …`.

- **A working MIDI backend** for python-rtmidi:
  - **macOS** — CoreMIDI works out of the box, nothing to install.
  - **Linux native** — ALSA is already there; the device is auto-detected.
  - **WSL2** — same setup you'd use for `osc-bridge`: forward the USB
    device with [`usbipd-win`](https://github.com/dorssel/usbipd-win)
    (`usbipd attach --wsl --busid <n>`), and confirm `/dev/snd/seq`
    exists in your WSL distro. If `osc-bridge list` shows the device,
    so will this MCP.
  - **Windows native** — WinMM works out of the box.

- **Node ≥ 18** if you want to use the bundler / screenshot tools
  (optional, only for the `screenshot_widget` tool).

## MCP tools exposed

| Tool | What it does |
|---|---|
| `electra.push_to_device(preset_path)` | Encode the preset JSON as SysEx and send it to the active slot over USB MIDI **directly via mido + python-rtmidi** (no external binary). No browser, no clicks. |
| `electra.get_device_logs(seconds)` | Listen on the CTRL port for `lua:` log messages from the device — print() output and fatal-error stack traces. Uses mido. |
| `electra.device_status()` | Query firmware version, current preset name/slot, and connected MIDI port via SysEx info request. Uses mido. |
| `electra.search_docs(query, kind)` | Full-text search of the mirrored documentation. `kind` filters by section (api / luacourse / troubleshooting / userguide). |
| `electra.get_api(symbol)` | Return the documented signature + parameters + example for a specific Lua API symbol (`graphics.print`, `parameterMap.onChange`, etc.). |
| `electra.list_constants(category)` | List enum constants by category — touch events (DOWN/MOVE/UP/CLICK/DOUBLECLICK), controller events, MIDI message types, alignments, models. |
| `electra.validate_preset(json)` | Check a preset JSON against schema + known-bad patterns (drawText, IIFE wraps, bitwise at load time, etc.). Returns a list of warnings. |
| `electra.bundle_widget(theme_path, primitives, widget_path)` | Concatenate theme + primitives + widget into a flat Lua blob suitable for the preset `lua` field. |
| `electra.screenshot_widget(slug, url)` | Run a headless Playwright capture of the emulator rendering the widget. |

## Repo layout

```
electra-one-mcp/
├── .claude-plugin/plugin.json       # Claude Code manifest
├── skills/
│   └── dev-electra-one/SKILL.md     # always-on Claude skill
├── server/
│   ├── server.py                    # MCP server — 20 tools
│   ├── win_bridge.py                # winmm/ctypes bridge + CLI for raw EL1 operations
│   ├── preset_converter.py          # tiles↔controls schema converter (ported from app.electra.one)
│   └── __init__.py / __main__.py
├── docs/
│   ├── DEV_GUIDE.md                 # user-facing widget-development guide
│   ├── RE_NOTES.md                  # reverse-engineering notes (bundle offsets, function ports)
│   ├── FORUM_REFERENCES.md          # quotes from Martin/kris explaining undocumented behaviour
│   ├── md/                          # raw markdown mirror of docs.electra.one (79 pages)
│   └── structured/
│       ├── api.json                 # 180 Lua API symbols → signature/params/returns/example
│       ├── constants.json           # all enum constants grouped by category (15 categories)
│       ├── gotchas.json             # device-side quirks confirmed empirically (17 entries)
│       ├── sysex_commands.json      # 62 SysEx commands (host→device + events) with bytes + payload
│       └── layout_constants.json    # MK2 + Mini layout dimensions + slot-bounds formulas
├── scripts/
│   ├── refresh-docs.sh              # re-scrape docs.electra.one
│   └── build-structured.py          # generate docs/structured/*.json from docs/md
└── examples/
    ├── push-and-watch.md            # tutorial: push + capture logs in one go
    └── overlay-recipe.md            # tutorial: customize a step list via overlay JSON
```

## Docs to read in order

1. **`docs/DEV_GUIDE.md`** — user-facing dev guide: the iteration loop, all 20 MCP tools, troubleshooting.
2. **`docs/md/developers/luaext.md`** — official Lua extension API (the surface you call from a preset's `main.lua`).
3. **`docs/md/developers/midiimplementation.md`** + **`docs/md/developers/filetransfer.md`** — official SysEx protocol.
4. **`docs/structured/sysex_commands.json`** — queryable catalog of 62 SysEx commands.
5. **`docs/RE_NOTES.md`** — what we reverse-engineered from `app.electra.one` and why (read this before modifying `preset_converter.py`).
6. **`docs/FORUM_REFERENCES.md`** — staff quotes for the undocumented behaviour (file transfer limits, schema mismatches, slot reload NACKs).

## Acknowledgements

- Electra One team (Martin Pavlas, Thomas Moravansky) for the hardware and the publicly-documented Lua API.
- Forum threads #4172, #4116, #4185, #592, #410, #2370, #2022, #807, #3590 for the staff quotes and confirmations that made this plugin possible.
- `xot/ElectraOne` (Codeberg), `johnnyclem/electra-one` and `elliotwoods/simularca-electra-one-plugin` as reference open-source consumers of the SysEx protocol.

## License

MIT.
