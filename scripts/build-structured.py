#!/usr/bin/env python3
"""Generate docs/structured/{api.json, constants.json} from docs/md/.

Parses the Electra One Lua API documentation (developers/luaext.md +
developers/presetformat.md + …) and extracts:

- api.json : every `<module>.<function>(args)` symbol with its
  signature, parameter list, return type, and example block (when present).
- constants.json : every enum-like list bullet group, grouped by category
  (touch events, controller events, MIDI message types, alignments,
  controller models, ports, parameter types).

The parser is intentionally simple — it pattern-matches the headings and
bullet shapes html2text produces from VitePress. If the upstream layout
changes, expect to revisit the regexes.

Run from the plugin root:
    python3 scripts/build-structured.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "md"
OUT = ROOT / "docs" / "structured"

# ---------- api.json ----------

# Heading lines look like: "***** <Module> ​ *****"  (5 stars = subsection title)
MODULE_RE = re.compile(r"^\*{4,6}\s*([A-Z][A-Za-z0-9 ]+)\s*[​\s]+\*{4,6}\s*$", re.MULTILINE)
# Functions appear as inline references: `function(args)` followed by a
# description paragraph, then `* Parameters *` etc. We capture lines that look
# like a Lua function call at the start of a paragraph.
FN_RE = re.compile(r"^([a-zA-Z_]+(?:\.[a-zA-Z_]+)+|<[a-z]+>:[a-zA-Z_]+)\(([^)]*)\)\s*$", re.MULTILINE)


def parse_api(md: str, source: str) -> dict[str, dict[str, Any]]:
    """Walk through one luaext.md / presetformat.md file and collect symbols."""
    symbols: dict[str, dict[str, Any]] = {}
    current_module = None
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Track current module heading
        m = MODULE_RE.match(line)
        if m:
            current_module = m.group(1).strip()
            i += 1
            continue
        # Function-call line
        fm = FN_RE.match(line)
        if fm:
            symbol = fm.group(1)
            args = fm.group(2).strip()
            # Pull the next non-empty lines until we hit a blank line or another
            # heading-like marker, treat them as the description.
            desc_lines = []
            j = i + 1
            while j < len(lines) and not lines[j].startswith("*"):
                if not lines[j].strip():
                    if desc_lines:
                        break
                else:
                    desc_lines.append(lines[j].strip())
                j += 1
            description = " ".join(desc_lines).strip()

            # Look ahead for "* Parameters *" or "* Returns *" or "** Example **"
            params: list[dict[str, str]] = []
            returns_desc = None
            example_lines: list[str] = []
            k = j
            while k < len(lines):
                ln = lines[k]
                # End of section: another function or another *** heading
                if FN_RE.match(ln) or MODULE_RE.match(ln):
                    break
                if ln.strip() == "* Parameters *":
                    k += 1
                    while k < len(lines) and not lines[k].startswith("*"):
                        pname = lines[k].strip()
                        k += 1
                        pdesc_parts = []
                        while k < len(lines) and lines[k].strip() and not lines[k].startswith("*"):
                            pdesc_parts.append(lines[k].strip())
                            k += 1
                        if pname:
                            params.append({"name": pname, "desc": " ".join(pdesc_parts)})
                    continue
                if ln.strip() == "* Returns *":
                    k += 1
                    ret_parts = []
                    while k < len(lines) and lines[k].strip() and not lines[k].startswith("*"):
                        ret_parts.append(lines[k].strip())
                        k += 1
                    returns_desc = " ".join(ret_parts)
                    continue
                if ln.strip().startswith("** Example") and "**" in ln.strip()[2:]:
                    k += 1
                    # The next 'lua' marker starts the code block in html2text output
                    if k < len(lines) and lines[k].strip() == "lua":
                        k += 1
                    while k < len(lines) and lines[k].strip() and not lines[k].startswith("*"):
                        example_lines.append(lines[k].rstrip())
                        k += 1
                    continue
                k += 1

            entry = {
                "module": current_module,
                "args": args,
                "description": description,
                "parameters": params,
                "returns": returns_desc,
                "example": "\n".join(example_lines) if example_lines else None,
                "source": source,
            }
            # If multiple definitions for the same symbol exist (rare), keep the
            # first one with an example, otherwise the first one we encountered.
            if symbol not in symbols or (entry["example"] and not symbols[symbol].get("example")):
                symbols[symbol] = entry
            i = max(j, i + 1)
            continue
        i += 1
    return symbols


def build_api() -> dict[str, Any]:
    files = [
        ("developers/luaext.md", "luaext"),
        ("developers/presetformat.md", "presetformat"),
    ]
    combined: dict[str, Any] = {}
    for rel, label in files:
        path = DOCS / rel
        if not path.exists():
            print(f"skip {rel} (not found)")
            continue
        md = path.read_text(encoding="utf-8")
        symbols = parse_api(md, source=rel)
        combined.update(symbols)
    print(f"api.json: extracted {len(combined)} symbols")
    return combined


# ---------- constants.json ----------

# A constants section in luaext.md looks like:
#   *** Touch events ​ ***
#   Identifiers of touch events used in the Touch callbacks.
#       * DOWN
#       * MOVE
#       * UP
#       * CLICK
#       * DOUBLECLICK
#   *** Curve segments ​ ***
#   ...

CONST_HEADING_RE = re.compile(r"^\*{3}\s*([A-Za-z][A-Za-z0-9 -]+?)\s*[​\s]+\*{3}\s*$")


def build_constants() -> dict[str, list[str]]:
    luaext = (DOCS / "developers" / "luaext.md").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    lines = luaext.split("\n")
    i = 0
    current_cat = None
    bullets: list[str] = []
    while i < len(lines):
        line = lines[i]
        m = CONST_HEADING_RE.match(line)
        if m:
            # Flush previous group if any
            if current_cat and bullets:
                out[current_cat] = bullets
            current_cat = m.group(1).strip()
            bullets = []
            i += 1
            continue
        # Bullet line: starts with whitespace + '*'
        bm = re.match(r"^\s+\*\s+([A-Z_][A-Z0-9_]*(?:\s*[|,]\s*[A-Z_][A-Z0-9_]*)*)\s*$", line)
        if bm and current_cat:
            bullets.append(bm.group(1).strip())
        i += 1
    if current_cat and bullets:
        out[current_cat] = bullets

    # Normalise keys to friendly category slugs
    rename = {
        "MIDI message types": "midi",
        "Controller events": "events",
        "Touch events": "touch",
        "Curve segments": "curve",
        "Controller models": "model",
        "Horizontal alignment": "align",
        "Vertical alignment": "valign",
        "MIDI port identifiers": "port",
        "Message parameter types": "ptype",
        "ParameterMap event origin": "origin",
    }
    final = {}
    for k, v in out.items():
        slug = rename.get(k, k.lower().replace(" ", "_"))
        final[slug] = v
    print(f"constants.json: extracted {len(final)} categories")
    return final


# ---------- main ----------

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    api = build_api()
    (OUT / "api.json").write_text(json.dumps(api, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    consts = build_constants()
    (OUT / "constants.json").write_text(json.dumps(consts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("done.")


if __name__ == "__main__":
    main()
