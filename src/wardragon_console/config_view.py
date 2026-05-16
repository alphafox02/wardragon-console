from __future__ import annotations

import configparser
from pathlib import Path
import os
import tempfile
import threading
import time
from typing import Any

from .settings import Settings
from .config_schema import GROUPS_BY_FILE, validate_updates


SENSITIVE_NAMES = ("password", "passwd", "secret", "token")
SECRET_PLACEHOLDER = "********"


ALLOWED_FILES = {"config.ini", "gps.ini"}

# Serialize all config writes so two concurrent saves cannot race on the
# read-modify-write cycle used by write_curated_config. RLock so the curated
# path can hold it across its own read-merge and write_config_file's write.
_WRITE_LOCK = threading.RLock()


def read_config_files(settings: Settings, include_raw: bool = False) -> dict[str, Any]:
    show_sensitive = settings.is_local_bind()
    files = [
        _read_ini(settings.dragonsync_dir / "config.ini", include_raw),
        _read_ini(settings.dragonsync_dir / "gps.ini", include_raw),
    ]
    return {
        "dragonsync_dir": str(settings.dragonsync_dir),
        "write_allowed": settings.can_write_config(),
        "restart_allowed": settings.can_restart_dragonsync(),
        "files": files,
        "forms": [_form_for_file(file, show_sensitive) for file in files],
    }


def write_config_file(settings: Settings, name: str, content: str) -> dict[str, Any]:
    if name not in ALLOWED_FILES:
        raise ValueError("unsupported config file")
    if not settings.can_write_config():
        raise PermissionError("config writes are disabled for this bind mode")
    if "\x00" in content:
        raise ValueError("config content contains a NUL byte")

    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise ValueError(f"invalid INI: {exc}") from exc

    normalized = content if content.endswith("\n") else content + "\n"
    if not normalized:
        normalized = "\n"

    path = settings.dragonsync_dir / name
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        # No-op short-circuit: if the file already matches byte-for-byte, skip
        # the write entirely. Avoids backup spam from rapid Save clicks and
        # from form re-submits where nothing actually changed.
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == normalized:
                    return {
                        "ok": True,
                        "file": name,
                        "path": str(path),
                        "backup_path": None,
                        "unchanged": True,
                    }
            except OSError:
                pass
        backup_path = None
        if path.exists():
            backup_path = path.with_name(f"{path.name}.bak.{time.strftime('%Y%m%d%H%M%S')}")
            backup_path.write_bytes(path.read_bytes())

        fd, tmp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(normalized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    return {
        "ok": True,
        "file": name,
        "path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
    }


def write_curated_config(settings: Settings, name: str, updates: dict[str, Any]) -> dict[str, Any]:
    if name not in ALLOWED_FILES:
        raise ValueError("unsupported config file")
    if name not in GROUPS_BY_FILE:
        raise ValueError("no curated schema for config file")
    if not settings.can_write_config():
        raise PermissionError("config writes are disabled for this bind mode")

    with _WRITE_LOCK:
        path = settings.dragonsync_dir / name
        parser = configparser.ConfigParser()
        parser.optionxform = str
        if path.exists():
            parser.read(path)

        fields = {field.key: field for group in GROUPS_BY_FILE[name] for field in group.fields}
        unknown = set(updates) - set(fields)
        if unknown:
            raise ValueError(f"unsupported keys: {', '.join(sorted(unknown))}")
        updates = {
            key: value
            for key, value in updates.items()
            if not (_is_sensitive(key) and str(value) == SECRET_PLACEHOLDER)
        }

        current_values: dict[str, Any] = {}
        for key, field in fields.items():
            if parser.has_section(field.section):
                current_values[key] = parser.get(field.section, key, fallback="")
            else:
                current_values[key] = ""
        current_values.update(updates)
        normalized = validate_updates(name, current_values)

        validated = {
            (fields[key].section, key): value
            for key, value in normalized.items()
            if key in updates
        }

        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        merged = _merge_ini_text(existing, validated)
        return write_config_file(settings, name, merged)


def _read_ini(path: Path, include_raw: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "exists": path.exists(),
        "sections": [],
        "form_sections": [],
        "error": None,
        "raw": None,
    }
    if not path.exists():
        return result

    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read(path)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if include_raw:
        try:
            result["raw"] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            result["raw"] = path.read_text(encoding="utf-8", errors="replace")

    sections: list[dict[str, Any]] = []
    form_sections: list[dict[str, Any]] = []
    if parser.defaults():
        sections.append({"name": "DEFAULT", "items": _items(parser.defaults())})
        form_sections.append({"name": "DEFAULT", "items": _raw_items(parser.defaults())})
    for section in parser.sections():
        sections.append({"name": section, "items": _items(dict(parser.items(section)))})
        form_sections.append({"name": section, "items": _raw_items(dict(parser.items(section)))})
    result["sections"] = sections
    result["form_sections"] = form_sections
    return result


def _items(values: dict[str, str]) -> list[dict[str, str]]:
    rendered = []
    for key, value in values.items():
        rendered.append({"key": key, "value": _mask(key, value)})
    return rendered


def _raw_items(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "value": value} for key, value in values.items()]


def _mask(key: str, value: str) -> str:
    if value and _is_sensitive(key):
        return SECRET_PLACEHOLDER
    return value


def _is_sensitive(key: str) -> bool:
    lower = key.lower()
    return any(name in lower for name in SENSITIVE_NAMES)


def _form_for_file(file_payload: dict[str, Any], show_sensitive: bool) -> dict[str, Any]:
    name = file_payload["name"]
    groups = GROUPS_BY_FILE.get(name, ())
    sections = {
        section["name"]: {item["key"]: item["value"] for item in section["items"]}
        for section in file_payload.get("form_sections", [])
    }
    form_groups = []
    for group in groups:
        form_fields = []
        for field in group.fields:
            value = sections.get(field.section, {}).get(field.key, "")
            if not show_sensitive:
                value = _mask(field.key, value)
            form_fields.append({
                "key": field.key,
                "label": field.label,
                "kind": field.kind,
                "section": field.section,
                "options": list(field.options),
                "help": field.help,
                "value": value,
            })
        form_groups.append({"title": group.title, "fields": form_fields})
    return {
        "name": name,
        "path": file_payload["path"],
        "exists": file_payload["exists"],
        "error": file_payload["error"],
        "groups": form_groups,
    }


def _merge_ini_text(existing: str, updates: dict[tuple[str, str], str]) -> str:
    lines = existing.splitlines()
    current_section = "DEFAULT"
    seen: set[tuple[str, str]] = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            _append_missing_for_section(out, current_section, updates, seen)
            current_section = stripped[1:-1].strip()
            out.append(line)
            continue
        key = _line_key(line)
        update_key = (current_section, key) if key else None
        if update_key in updates:
            out.append(f"{key} = {updates[update_key]}")
            seen.add(update_key)
        else:
            out.append(line)

    _append_missing_for_section(out, current_section, updates, seen)
    missing_sections = sorted({section for section, _key in updates if (section, _key) not in seen and section != current_section})
    for section in missing_sections:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        _append_missing_for_section(out, section, updates, seen)

    return "\n".join(out).rstrip() + "\n"


def _append_missing_for_section(
    out: list[str],
    section: str,
    updates: dict[tuple[str, str], str],
    seen: set[tuple[str, str]],
) -> None:
    pending = [(key, value) for (candidate_section, key), value in updates.items()
               if candidate_section == section and (candidate_section, key) not in seen]
    if not pending:
        return
    if out and out[-1].strip():
        out.append("")
    for key, value in pending:
        out.append(f"{key} = {value}")
        seen.add((section, key))


def _line_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")):
        return None
    if "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()
