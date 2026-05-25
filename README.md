# electra-one-mcp

An MCP server + Claude Code plugin for developing custom widgets and presets on the [Electra One](https://electra.one) MK2 / Mini MIDI controller, with three integrated parts:

1. **Skill `dev-electra-one`** — primes Claude with the verified Lua API + the device-side gotchas learned the hard way (graphics.print signature, integer coords, 1-pot-per-custom-tile firmware limit, IIFE-free bundling, etc.).
2. **MCP server `electra-one`** — exposes nine tools so Claude can push presets, capture logs, search docs, validate, and bundle, all without you clicking around in `app.electra.one`.
3. **Indexed docs** — the full official documentation mirror (79 pages, including the MIDI & Lua crash course) plus a machine-readable distillation (`docs/structured/api.json`, `constants.json`, `gotchas.json`).

## Why this exists

The Electra One Lua extension is powerful but its documentation is split across `docs.electra.one`, the forum, and tribal knowledge. We've lost hours rediscovering things like "graphics.drawText doesn't exist on device, use graphics.print" or "the `inputs` array in the preset JSON is silently ignored for `type:"custom"` tiles". This plugin packages those findings + the official docs so future sessions don't repay the discovery tax.

## Install

```bash
claude plugin install gh:roomi-fields/electra-one-mcp
```

After install:
- Activate the skill: it'll trigger automatically whenever you work on Electra One Lua/preset code.
- Restart Claude Code so the MCP server is registered.
- Plug your Electra One in via USB and ensure `sendmidi` is on your PATH (see "Prerequisites" below).

## Prerequisites

- **Python ≥ 3.10** for the MCP server.
- **`sendmidi`** (https://github.com/gbevin/SendMIDI) for the device push tool — `brew install sendmidi` on macOS, prebuilt binaries for Windows/Linux on the GitHub releases page.
- **Node ≥ 18** if you want to use the bundler / screenshot tools (optional).

## MCP tools exposed

| Tool | What it does |
|---|---|
| `electra.push_to_device(preset_path)` | Encode the preset JSON as SysEx and send it to the active slot over USB MIDI. No browser, no clicks. |
| `electra.get_device_logs(seconds)` | Listen on the CTRL port for `lua:` log messages from the device — print() output and fatal-error stack traces. |
| `electra.device_status()` | Query firmware version, current preset name/slot, and connected MIDI port. |
| `electra.search_docs(query, kind)` | Full-text search of the mirrored documentation. `kind` filters by section (api / luacourse / troubleshooting / userguide). |
| `electra.get_api(symbol)` | Return the documented signature + parameters + example for a specific Lua API symbol (`graphics.print`, `parameterMap.onChange`, etc.). |
| `electra.list_constants(category)` | List enum constants by category — touch events (DOWN/MOVE/UP/CLICK/DOUBLECLICK), controller events, MIDI message types, alignments, models. |
| `electra.validate_preset(json)` | Check a preset JSON against schema + known-bad patterns (drawText, IIFE wraps, bitwise at load time, etc.). Returns a list of warnings. |
| `electra.bundle_widget(theme_path, primitives, widget_path)` | Concatenate theme + primitives + widget into a flat Lua blob suitable for the preset `lua` field. |
| `electra.screenshot_widget(slug, url)` | Run a headless Playwright capture of the emulator rendering the widget. |

## Repo layout

```
electra-one-mcp/
├── plugin.json
├── skills/
│   └── dev-electra-one/SKILL.md     # always-on Claude skill
├── mcp/
│   ├── __main__.py                  # `python -m mcp_electra_one`
│   ├── server.py                    # MCP server using the official `mcp` SDK
│   └── tools/                       # one file per tool
├── docs/
│   ├── md/                          # raw markdown mirror of docs.electra.one (79 pages)
│   └── structured/
│       ├── api.json                 # Lua API symbols → signature/params/returns/example
│       ├── constants.json           # all enum constants grouped by category
│       └── gotchas.json             # device-side quirks confirmed empirically
├── scripts/
│   ├── refresh-docs.sh              # re-scrape docs.electra.one
│   └── build-structured.py          # generate docs/structured/*.json from docs/md
└── examples/
    ├── push-and-watch.md            # tutorial: push + capture logs in one go
    └── overlay-recipe.md            # tutorial: customize a step list via overlay JSON
```

## Acknowledgements

- Electra One team (Martin Pavlas, Thomas Moravansky) for the hardware and the publicly-documented Lua API.
- Forum threads #4172, #4116, #4185 for confirming the firmware limits we hit in practice.

## License

MIT.
