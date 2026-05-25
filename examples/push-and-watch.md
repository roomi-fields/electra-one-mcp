# Tutorial: push a widget and watch the logs

A typical iteration loop when developing a custom-paint widget for the Electra One looks like this:

1. Edit the widget Lua.
2. Bundle it with `lib/theme.lua` + any primitives.
3. Push the bundled preset to the device.
4. Watch the `lua:` logs to confirm the widget loaded and to catch any fatal-error stack trace.

Before this plugin, all four steps were manual. With the MCP server, Claude can do them for you.

## Prerequisites

- The Electra One plugged in via USB.
- `sendmidi` and `receivemidi` on your PATH (https://github.com/gbevin/SendMIDI).
- The device's **Logger** enabled in the Lua tab of `app.electra.one` (default is OFF "for performance reasons" per the docs).

## End-to-end loop

Given a widget at `widgets/my-widget/widget.lua` and a `lib/theme.lua` + selected primitives:

```text
"Bundle my-widget, push it, and tell me what the device logs say in the next 3 seconds."
```

Claude will call:

```text
electra.bundle_widget(
  theme_path="lib/theme.lua",
  primitive_paths=["lib/primitives/grid.lua", "lib/primitives/button.lua"],
  widget_path="widgets/my-widget/widget.lua"
)
```

then write the resulting `lua` field into `widgets/my-widget/demo.preset.json`, then:

```text
electra.push_to_device(preset_path="widgets/my-widget/demo.preset.json")
electra.get_device_logs(seconds=3)
```

If the device logs include a fatal-error stack trace, Claude will read the line number, jump into the widget Lua, and propose a fix.

## What to expect when something is wrong

Common silent-failure symptoms and what the logs will say:

| Logs say | Fix |
|---|---|
| (nothing) | Logger probably off. Toggle it in the Lua tab. |
| `error running function 'runPaintCallback': … attempt to call a nil value (field 'drawText')` | You used `graphics.drawText`. Replace with `graphics.print(x, y, text, w, alignment)`. See gotchas.json #drawText-does-not-exist. |
| `error running function 'runPaintCallback': … number has no integer representation` | Float coord. Wrap in `math.floor(...)`. See #integer-coords-required. |
| `error running function 'onLoad': … attempt to call a nil value (field 'onChange')` | `parameterMap.onChange` is a global you DEFINE, not call. See #parametermap-onchange-is-global-fn. |
| Widget renders as a flat blue bar | Could be IIFE wrap (#iife-breaks-upvalues) or a local-graphics cache (#local-graphics-upvalue) or a closure callback (#callbacks-must-be-globals). |

## Verifying the device picked up your version

Add a `WIDGET_REV = "001"` constant at the top of your widget.lua and display it in the lane header:

```lua
graphics.setColor(Theme.NEUTRAL_ACCENT)
graphics.print(W - 30, 6, "r" .. WIDGET_REV, 9999, LEFT)
```

Bump the number on every push. The visible `r001` / `r002` / … in the header lets you confirm the upload actually replaced the previous bundle (not a browser cache or a stale slot).
