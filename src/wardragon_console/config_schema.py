from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"
    section: str = "SETTINGS"
    options: tuple[str, ...] = ()
    help: str = ""
    min_value: float | None = None
    max_value: float | None = None


@dataclass(frozen=True)
class Group:
    title: str
    fields: tuple[Field, ...]


CONFIG_GROUPS: tuple[Group, ...] = (
    Group("DragonSync API", (
        Field("api_enabled", "Enabled", "bool", help="Enable DragonSync's read-only HTTP API used by this console and the ATAK plugin."),
        Field("api_host", "Bind host", help="DragonSync API listen address. 0.0.0.0 listens on all interfaces."),
        Field("api_port", "Port", "int", help="DragonSync API port. Default is 8088.", min_value=1, max_value=65535),
    )),
    Group("TAK Server", (
        Field("tak_host", "Host", help="TAK Server hostname or IP. Leave host and port blank to disable direct TAK Server output."),
        Field("tak_port", "Port", "int", help="TAK Server port. Host and port must be set together.", min_value=1, max_value=65535),
        Field("tak_protocol", "Protocol", "select", options=("", "TCP", "UDP"), help="TCP or UDP. Leave disabled when host/port are blank."),
        Field("enable_multicast", "Multicast enabled", "bool", help="Send CoT to local TAK multicast. This is the simple no-server option."),
        Field("tak_multicast_addr", "Multicast address", help="TAK SA multicast group. Default is 239.2.3.1."),
        Field("tak_multicast_port", "Multicast port", "int", help="TAK SA multicast port. Default is 6969.", min_value=1, max_value=65535),
        Field("tak_multicast_interface", "Multicast interface", help="Interface/IP for multicast. 0.0.0.0 sends on all active interfaces."),
        Field("multicast_ttl", "Multicast TTL", "int", help="Multicast hop limit. Use 1 for local network only.", min_value=1, max_value=255),
    )),
    Group("TAK TLS Paths", (
        Field("tak_tls_p12", "PKCS#12 file", help="Path to client .p12 certificate for TAK TCP/TLS. Use either P12 or PEM cert+key, not both."),
        Field("tak_tls_p12_pass", "PKCS#12 password", "password", help="Password for the P12 file, if required."),
        Field("tak_tls_certfile", "PEM cert file", help="Path to PEM client certificate. Requires PEM key file."),
        Field("tak_tls_keyfile", "PEM key file", help="Path to PEM private key. Requires PEM cert file."),
        Field("tak_tls_cafile", "CA file", help="Optional CA certificate for server verification."),
        Field("tak_tls_skip_verify", "Skip verification", "bool", help="Unsafe: disable TAK server TLS verification. Useful only for testing."),
    )),
    Group("Runtime", (
        Field("rate_limit", "CoT rate limit", "float", help="Minimum seconds between CoT sends per drone.", min_value=0.001),
        Field("inactivity_timeout", "Drone inactivity timeout", "float", help="Seconds before an inactive drone track is removed.", min_value=0.001),
        Field("max_verified_drones", "Max verified drones", "int", help="Capacity for drones that pass FAA RID lookup; evicted last.", min_value=1),
        Field("max_unverified_drones", "Max unverified drones", "int", help="Capacity for unverified drones.", min_value=1),
        Field("rid_api_enabled", "FAA RID API fallback", "bool", help="Allow online FAA API fallback when local RID database misses."),
    )),
    Group("MQTT", (
        Field("mqtt_enabled", "Enabled", "bool", help="Master switch for MQTT output."),
        Field("mqtt_host", "Host", help="MQTT broker hostname or IP."),
        Field("mqtt_port", "Port", "int", help="MQTT broker port. Default is 1883.", min_value=1, max_value=65535),
        Field("mqtt_topic", "Drone topic", help="Aggregate topic for drone updates."),
        Field("mqtt_username", "Username", help="Optional MQTT username."),
        Field("mqtt_password", "Password", "password", help="Optional MQTT password."),
        Field("mqtt_tls", "TLS", "bool", help="Enable TLS connection to MQTT broker."),
        Field("mqtt_ca_file", "CA file", help="Optional CA file for MQTT TLS."),
        Field("mqtt_certfile", "Client cert file", help="Optional MQTT client certificate for mTLS."),
        Field("mqtt_keyfile", "Client key file", help="Optional MQTT client key for mTLS."),
        Field("mqtt_tls_insecure", "TLS insecure", "bool", help="Unsafe: skip MQTT TLS verification."),
        Field("mqtt_retain", "Retain messages", "bool", help="Retain state messages, useful for Home Assistant dashboards."),
    )),
    Group("Home Assistant", (
        Field("mqtt_per_drone_enabled", "Per-drone topics", "bool", help="Publish wardragon/drone/<id> topics. Required for Home Assistant discovery."),
        Field("mqtt_per_drone_base", "Per-drone base", help="Base topic for per-drone state."),
        Field("mqtt_ha_enabled", "Discovery enabled", "bool", help="Publish Home Assistant MQTT Discovery configs."),
        Field("mqtt_ha_prefix", "Discovery prefix", help="Home Assistant discovery prefix, usually homeassistant."),
        Field("mqtt_ha_device_base", "Device base", help="Base used for HA device unique IDs."),
        Field("mqtt_signals_enabled", "Signal topic enabled", "bool", help="Publish FPV/RF signal alerts to MQTT when signal pipeline is enabled."),
        Field("mqtt_signals_topic", "Signal topic", help="Aggregate MQTT topic for FPV/RF signal alerts."),
        Field("mqtt_ha_signal_tracker", "Signal tracker", "bool", help="Create a Home Assistant signal alert tracker."),
        Field("mqtt_ha_signal_id", "Signal ID", help="Unique ID suffix for the signal tracker entity."),
    )),
    Group("FPV Signals", (
        Field("fpv_enabled", "Enabled", "bool", help="Enable DragonSig FPV/RF signal ingestion through DragonSync."),
        Field("fpv_zmq_host", "ZMQ host", help="Host for DragonSig FPV alert ZMQ stream."),
        Field("fpv_zmq_port", "ZMQ port", "int", help="Port for DragonSig FPV alert ZMQ stream. Default is 4226.", min_value=1, max_value=65535),
        Field("fpv_stale", "Stale seconds", "float", help="Seconds before a signal alert expires.", min_value=0.001),
        Field("fpv_radius_m", "Alert radius", "float", help="Offset radius for alert dot near kit position.", min_value=0),
        Field("fpv_rate_limit", "Rate limit", "float", help="Minimum seconds between CoT sends for the same signal.", min_value=0.001),
        Field("fpv_max_signals", "Max signals", "int", help="Maximum concurrent signal alerts.", min_value=1),
        Field("fpv_confirm_only", "Confirm only", "bool", help="Only ingest confirmed alerts, not raw energy hits."),
    )),
)

GPS_GROUPS: tuple[Group, ...] = (
    Group("GPS", (
        Field("use_static_gps", "Use static GPS", "bool", section="gps", help="Use fixed coordinates instead of live GPS when enabled."),
        Field("static_lat", "Static latitude", "float", section="gps", help="Fixed latitude in decimal degrees.", min_value=-90, max_value=90),
        Field("static_lon", "Static longitude", "float", section="gps", help="Fixed longitude in decimal degrees.", min_value=-180, max_value=180),
        Field("static_alt", "Static altitude", "float", section="gps", help="Fixed altitude in meters."),
    )),
)


GROUPS_BY_FILE = {
    "config.ini": CONFIG_GROUPS,
    "gps.ini": GPS_GROUPS,
}


def field_map(name: str) -> dict[str, Field]:
    result: dict[str, Field] = {}
    for group in GROUPS_BY_FILE[name]:
        for field in group.fields:
            result[field.key] = field
    return result


def validate_value(field: Field, value: Any) -> str:
    if value is None:
        return ""
    if field.kind == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        if text not in {"true", "false", "1", "0", "yes", "no", "on", "off", ""}:
            raise ValueError(f"{field.key} must be boolean")
        return "true" if text in {"true", "1", "yes", "on"} else "false"
    if field.kind == "int":
        text = str(value).strip()
        if text == "":
            return ""
        number = int(text)
        _check_range(field, number)
        return text
    if field.kind == "float":
        text = str(value).strip()
        if text == "":
            return ""
        number = float(text)
        _check_range(field, number)
        return text
    if field.kind == "select":
        text = str(value).strip()
        normalized = text.upper() if field.key == "tak_protocol" else text
        if normalized not in field.options:
            raise ValueError(f"{field.key} must be one of {', '.join(repr(v) for v in field.options)}")
        return normalized
    return str(value).strip()


def validate_updates(name: str, values: dict[str, Any]) -> dict[str, str]:
    fields = field_map(name)
    normalized: dict[str, str] = {}
    unknown = set(values) - set(fields)
    if unknown:
        raise ValueError(f"unsupported keys: {', '.join(sorted(unknown))}")
    for key, value in values.items():
        normalized[key] = validate_value(fields[key], value)

    if name == "config.ini":
        _validate_config_ini(normalized)
    elif name == "gps.ini":
        _validate_gps_ini(normalized)
    return normalized


def _validate_config_ini(values: dict[str, str]) -> None:
    tak_host = values.get("tak_host", "")
    tak_port = values.get("tak_port", "")
    tak_protocol = values.get("tak_protocol", "")
    if bool(tak_host) != bool(tak_port):
        raise ValueError("TAK host and TAK port must be set together")
    if tak_host and tak_port:
        if tak_protocol not in {"TCP", "UDP"}:
            raise ValueError("TAK protocol must be TCP or UDP when TAK host/port are set")
        if tak_protocol == "TCP":
            has_p12 = bool(values.get("tak_tls_p12", ""))
            has_pem = bool(values.get("tak_tls_certfile", "")) and bool(values.get("tak_tls_keyfile", ""))
            if not has_p12 and not has_pem:
                raise ValueError("TAK TCP requires either a PKCS#12 file or both PEM cert and PEM key")
            if has_p12 and (values.get("tak_tls_certfile") or values.get("tak_tls_keyfile")):
                raise ValueError("Use either TAK PKCS#12 or PEM cert/key, not both")

    if _truthy(values.get("enable_multicast", "false")):
        if not values.get("tak_multicast_addr"):
            raise ValueError("Multicast address is required when multicast is enabled")
        if not values.get("tak_multicast_port"):
            raise ValueError("Multicast port is required when multicast is enabled")

    if _truthy(values.get("mqtt_enabled", "false")):
        if not values.get("mqtt_host"):
            raise ValueError("MQTT host is required when MQTT is enabled")
        if not values.get("mqtt_port"):
            raise ValueError("MQTT port is required when MQTT is enabled")

    if _truthy(values.get("mqtt_ha_enabled", "false")) and not _truthy(values.get("mqtt_per_drone_enabled", "false")):
        raise ValueError("Home Assistant discovery requires per-drone topics")

    if _truthy(values.get("mqtt_ha_signal_tracker", "false")) and not _truthy(values.get("mqtt_signals_enabled", "false")):
        raise ValueError("Home Assistant signal tracker requires MQTT signal topic enabled")

    if _truthy(values.get("fpv_enabled", "false")):
        if not values.get("fpv_zmq_host"):
            raise ValueError("FPV ZMQ host is required when FPV is enabled")
        if not values.get("fpv_zmq_port"):
            raise ValueError("FPV ZMQ port is required when FPV is enabled")


def _validate_gps_ini(values: dict[str, str]) -> None:
    if _truthy(values.get("use_static_gps", "false")):
        for key in ("static_lat", "static_lon", "static_alt"):
            if values.get(key, "") == "":
                raise ValueError(f"{key} is required when static GPS is enabled")


def _check_range(field: Field, value: float) -> None:
    if field.min_value is not None and value < field.min_value:
        raise ValueError(f"{field.key} must be at least {field.min_value:g}")
    if field.max_value is not None and value > field.max_value:
        raise ValueError(f"{field.key} must be at most {field.max_value:g}")


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes", "on"}
