"""Carry operator-changed INI values across an upstream update.

Given three versions of a file — ``base`` (the upstream version the operator
started from), ``local`` (the operator's current file) and ``new`` (the
incoming upstream version) — produce ``new`` with every value the operator
changed from ``base`` written back in. Comments, ordering and keys the
operator never touched come from ``new``.
"""
from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field

# Treat a literal [DEFAULT] header as an ordinary section rather than letting
# configparser fold its keys into every other section.
_NO_DEFAULT_SECTION = "\x00wardragon-no-default\x00"
_SECTION_RE = re.compile(r"\[(?P<header>.+)\]")
_OPTION_RE = re.compile(r"(?P<key>.*?)\s*(?P<sep>[=:])\s*(?P<value>.*)$")


class ReconcileError(ValueError):
    pass


@dataclass
class ReconcileResult:
    text: str
    preserved: list[tuple[str, str]] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)


def parse_ini(text: str) -> dict[tuple[str, str], str]:
    parser = configparser.ConfigParser(
        interpolation=None,
        strict=False,
        default_section=_NO_DEFAULT_SECTION,
    )
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ReconcileError(f"cannot parse INI: {exc}") from exc
    values: dict[tuple[str, str], str] = {}
    for section in parser.sections():
        for key, value in parser.items(section, raw=True):
            values[(section, key)] = value
    return values


def reconcile(base: str, local: str, new: str) -> ReconcileResult:
    base_values = parse_ini(base)
    local_values = parse_ini(local)
    new_values = parse_ini(new)

    updates: dict[tuple[str, str], str] = {}
    preserved: list[tuple[str, str]] = []
    conflicts: list[tuple[str, str]] = []
    for item, value in local_values.items():
        if base_values.get(item) == value:
            continue
        if new_values.get(item) == value:
            continue
        if item in base_values and item in new_values and new_values[item] != base_values[item]:
            conflicts.append(item)
        updates[item] = value
        preserved.append(item)

    text = _apply(new, updates)

    merged = parse_ini(text)
    for item, value in updates.items():
        if merged.get(item) != value:
            raise ReconcileError(f"failed to carry over {item[0]}.{item[1]}")
    for item, value in new_values.items():
        if item not in updates and merged.get(item) != value:
            raise ReconcileError(f"merge altered untouched key {item[0]}.{item[1]}")

    return ReconcileResult(text=text, preserved=preserved, conflicts=conflicts)


def _apply(text: str, updates: dict[tuple[str, str], str]) -> str:
    by_section: dict[str, dict[str, str]] = {}
    for (section, key), value in updates.items():
        by_section.setdefault(section, {})[key] = value

    lines = text.splitlines()
    out: list[str] = []
    written: set[tuple[str, str]] = set()
    section: str | None = None
    i = 0

    def append_missing(current: str | None) -> None:
        if current is None:
            return
        missing = [
            (key, value)
            for key, value in by_section.get(current, {}).items()
            if (current, key) not in written
        ]
        if not missing:
            return
        trailing: list[str] = []
        while out and not out[-1].strip():
            trailing.append(out.pop())
        for key, value in missing:
            out.extend(_format(key, value, "="))
            written.add((current, key))
        out.extend(reversed(trailing))

    while i < len(lines):
        line = lines[i]
        header = _SECTION_RE.match(line.strip()) if line[:1] not in (" ", "\t") else None
        if header:
            append_missing(section)
            section = header.group("header").strip()
            out.append(line)
            i += 1
            continue

        parsed = _option(line)
        if section is not None and parsed is not None and parsed[0] in by_section.get(section, {}):
            key, sep = parsed
            out.extend(_format(key, by_section[section][key], sep))
            written.add((section, key))
            i += 1
            # Skip continuation lines that belonged to the old value.
            while i < len(lines) and lines[i][:1] in (" ", "\t") and lines[i].strip() \
                    and not lines[i].strip().startswith(("#", ";")):
                i += 1
            continue

        out.append(line)
        i += 1

    append_missing(section)

    for current, keys in by_section.items():
        remaining = [(k, v) for k, v in keys.items() if (current, k) not in written]
        if not remaining:
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{current}]")
        for key, value in remaining:
            out.extend(_format(key, value, "="))
            written.add((current, key))

    return "\n".join(out).rstrip("\n") + "\n"


def _option(line: str) -> tuple[str, str] | None:
    if not line or line[:1] in (" ", "\t"):
        return None
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")):
        return None
    match = _OPTION_RE.match(stripped)
    if not match:
        return None
    key = match.group("key").strip()
    if not key:
        return None
    return key, match.group("sep")


def _format(key: str, value: str, sep: str) -> list[str]:
    first, *rest = value.split("\n")
    joiner = " = " if sep == "=" else ": "
    lines = [f"{key}{joiner}{first}".rstrip()]
    lines.extend(f"    {part}" for part in rest)
    return lines


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Carry INI settings across an upstream update.")
    parser.add_argument("base")
    parser.add_argument("local")
    parser.add_argument("new")
    parser.add_argument("output")
    args = parser.parse_args(argv)

    def read(path: str) -> str:
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    try:
        result = reconcile(read(args.base), read(args.local), read(args.new))
    except (OSError, UnicodeDecodeError, ReconcileError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(result.text)
    json.dump({"preserved": result.preserved, "conflicts": result.conflicts}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
