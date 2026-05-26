# Forum references — quotes from staff and threads we mined

Capturing the forum.electra.one threads + staff quotes that influenced this plugin's design. Many of the constraints we work around aren't in the official docs — they only come from Martin Pavlas (founder) and kris (staff) posts in support threads. Saving them here so they survive even if the forum reorganises.

## File transfer + big preset push

### Thread #592 — "Command line preset file upload tool"
URL: https://forum.electra.one/t/command-line-preset-file-upload-tool/592

**Post #9, martin (2021-04-26)** — recommends keeping chunks small + acknowledges the difficulty:
> "I would keep the chunks well under 4kB. That would nicely resolve the output buffer size issue on linux too… MIDI SDS (127-byte packets)… would improve the reliability of the transfers."

He adds:
> "it seems to be close to impossible"

— referring to reliably pushing large data over MIDI. He notes he succeeded with 600 KB firmware uploads only via HID where he had *"control over every single packet."*

**Post #16, kris** — confirms the chunking guidance:
> "splitting preset transfer into smaller chunks certainly will make less headache in the future."

Also notes the only user-visible failure mode is:
> "Preset Transfer failed, please retry"

— and that the device *"will always display"* this even when ACKs came back during the upload (relevant: failure can surface late).

**Why we cite this**: Martin's "chunks well under 4kB" recommendation is the reason our `push_to_device` doesn't try to brute-force a 25 KB preset in a single `01 01` SysEx. The web editor circumvents the size issue by splitting `tile.lua` into a separate `01 0C` upload (the preset.json without lua is ~500 bytes); we do the same in `preset_converter.split_preset_for_upload()`.

### Thread #410 — "Preset Transfer Failed Please Retry SOLVED"
URL: https://forum.electra.one/t/preset-transfer-failed-please-retry-solved/410

**Martin, post 2** — confirms a known bug:
> "this is a known bug. It happens when transferring large presets… It is confirmed that it works on the second or third try."

Root cause attributed to:
> "Chrome flooding Electra with too much data."

**Why we cite this**: explains why even the web editor sometimes needs a retry on large uploads. We accept this and surface failures clearly via `push_to_device`'s NACK detection.

### Thread #2370 — "Preset size limitations"
URL: https://forum.electra.one/t/preset-size-limitations/2370

**Martin** — gives the parser's working set:
> "The parsing area is now 300k. I will increase it."

And:
> "[Lua heap is] around 20MB now, shared by all presets."

Confirmed failure signatures in firmware logs:
- `Preset::load: parsing failed`
- `Preset::load: cannot open preset file: ctrlv2/p000.epr`

**Why we cite this**: tells us our 25 KB step-seq-16 + 40 KB Lua is well inside the parser's budget. If a user hits a real limit, the firmware logs (via `device_state` → `log`) will say so.

### Thread #2022 — "Can't transfer presets, device won't reboot"
URL: https://forum.electra.one/t/cant-transfer-presets-device-wont-reboot/2022

**Martin** — recovery procedure when the default preset itself is broken:
> "When the default preset is causing problems (eg. infinite loop in Lua or something like that)"

— device may silently fail to load. Recovery: boot with top-left held to skip preset load.

**Why we cite this**: if a user's Lua has an infinite loop, the device may not respond to MIDI at all. `device_state` returns empty and the user thinks the MCP is broken — actually the device is locked in their Lua. The hardware reset procedure is the only out.

## Lua extension API quirks

### Thread #4172 — "Multi-value custom controls"
URL: https://forum.electra.one/t/multi-value-custom-controls

Open feature request. Custom tiles only receive pot events from ONE pot, the one mapped to one of their `values[]`. Martin acknowledged the request; not yet shipped as of firmware 4.1.4.

**Why we cite this**: a tile in our repo can declare multiple `values` but only the first one's pot dispatches events. This forced our note-list-16 widget to use a single-pot + mode-toggle pattern instead of the "2 encoders per control" the original spec asked for.

### Threads #4116 + #4185 — "Encoder press vs touch"
- #4116: https://forum.electra.one/t/encoder-press-event/4116
- #4185: https://forum.electra.one/t/distinguishing-touch-from-click

Both confirm:
> "no `onPotClick` exists"

`events.onPotTouch` fires on capacitive touch but cannot distinguish a click from the start of a turn. The pattern we use everywhere: track DOWN/UP timestamps and call it a "click" when no MOVE event arrives between them.

### Thread #4058 — "Silent Lua compilation failure at ~265 KB"
URL: https://forum.electra.one/t/lua-script-too-big/4058

On firmware 4.1.2, Lua sources above ~265 KB fail to compile silently — no error message reaches the host. We haven't hit it (our biggest bundle is ~40 KB) but the threshold exists.

### Thread #4259 — "Firmware 4.1.4 release notes"
URL: https://forum.electra.one/t/firmware-4-1-4-release/4259

The changelog for our baseline firmware:
- Added fatal-error Lua handler
- Fixed RGB888→RGB565 translation for preset bank colours
- Fixed memory leak in `controls.get()`

**Why we cite this**: confirms that colors in our theme.lua should be passed as 24-bit RGB888 (`0xRRGGBB`) — the firmware does the conversion to the panel's 16-bit format. Don't pre-convert in user code.

### Thread #807 — "LUA & Patch Request"
URL: https://forum.electra.one/t/lua-patch-request/807

**Martin** acknowledges:
> "ahh, that is a good one. I will check."

— in response to a report that Lua scripts sometimes don't run after a slot switch.

**Why we cite this**: explains the empirical need for the slot-flip reload trick. The device's runtime sometimes keeps the previous Lua state when switching to a newly-uploaded slot. Flipping away then back forces a clean reinit.

### Thread #3590 — "Develop lua in external editor not browser"
URL: https://forum.electra.one/t/develop-lua-in-external-editor-not-browser/3590

**Martin** — confirms `01 0C` works for live Lua iteration without needing the web editor:
> "given that you have a sendmidi tool installed, you can use a simple shell script… The controller will execute the newly uploaded Lua immediately after the upload is completed."

**Why we cite this**: this is the basis of our `push_to_device`'s `01 0C` Lua upload path. Martin himself recommends it; we automate it.

## Cross-references with the SysEx protocol

Many forum threads describe behaviour that's documented at `docs.electra.one/developers/midiimplementation.html` but with crucial firmware-version caveats:

- **Transaction IDs**: docs say firmware ≥ 4.0 supports them. We confirmed on 4.1.4: every command's ACK echoes the txid, NACK does too. Use them on any new command class — without them you cannot correlate ACKs to commands during File Transfer chunks.

- **`08 08 Reload Preset Slot`**: docs list it as no-args. On firmware 4.1.4 it NACKs reliably; we use the slot-flip workaround instead. Posted nowhere on the forum that I could find — empirical finding from the 2026-05-26 session.

- **`14 79 Subscribe Events` bitmask**: docs claim 7 bits (page, controlset, usb, pots, touch, button, window). Empirically (firmware 4.1.4): pots + touch + button work; the others may or may not depending on firmware build.

## Reference repositories

Open-source consumers of the EL1 SysEx protocol we used as cross-references:

- **xot/ElectraOne** — Codeberg, Ableton Live remote script. Uses `01 01 Upload Preset` + shells out to `sendmidi` for the lua. https://codeberg.org/xot/ElectraOne
- **johnnyclem/electra-one** — GitHub, Node CLI tool. Uses `01 01` + `01 0C` directly. https://github.com/johnnyclem/electra-one
- **elliotwoods/simularca-electra-one-plugin** — GitHub, TypeScript driver. Same `01 01` + `01 0C` pattern, header comment says *"all framings here are CONFIRMED against the Electra docs"*. https://github.com/elliotwoods/simularca-electra-one-plugin

None of these implement the File Transfer cache API (`01 2D / 2E / 2F / 04 2D`). Our plugin is the first public implementation we found that wraps it (mode=`ft`), although in practice we default to `simple` mode because FT commit silently rolls back on this firmware.
