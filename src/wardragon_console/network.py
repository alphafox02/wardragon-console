from __future__ import annotations

import socket
import struct
from fcntl import ioctl
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any


SIOCGIFADDR = 0x8915


def ipv4_interfaces() -> list[dict[str, str]]:
    interfaces: list[dict[str, str]] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return interfaces
    try:
        for _idx, name in socket.if_nameindex():
            if name == "lo":
                continue
            try:
                packed = struct.pack("256s", name[:15].encode("utf-8"))
                address = socket.inet_ntoa(ioctl(sock.fileno(), SIOCGIFADDR, packed)[20:24])
            except OSError:
                continue
            details = interface_details(name)
            interfaces.append({"name": name, "ipv4": address, **details})
    finally:
        sock.close()
    return interfaces


DEFAULT_TETHER_CIDRS = ("192.168.42.0/24", "192.168.43.0/24", "172.20.10.0/28")


def tether_candidates(allowed_cidrs: tuple[str, ...] = DEFAULT_TETHER_CIDRS) -> list[dict[str, Any]]:
    candidates = []
    for interface in ipv4_interfaces():
        if is_tether_interface(interface, allowed_cidrs):
            candidates.append(interface)
    return candidates


def is_tether_interface(interface: dict[str, Any], allowed_cidrs: tuple[str, ...] = DEFAULT_TETHER_CIDRS) -> bool:
    name = str(interface.get("name", ""))
    ipv4 = str(interface.get("ipv4", ""))
    if not name or not ipv4 or ipv4.startswith("127."):
        return False
    if ipv4.startswith("169.254."):
        return False
    if not _ip_in_allowed_cidrs(ipv4, allowed_cidrs):
        return False

    driver = str(interface.get("driver", ""))
    if driver in {"rndis_host", "cdc_ether", "cdc_ncm", "ipheth"}:
        return True
    if interface.get("usb_vendor") == "05ac":
        return True
    return False


def _ip_in_allowed_cidrs(ipv4: str, allowed_cidrs: tuple[str, ...]) -> bool:
    try:
        address = ip_address(ipv4)
    except ValueError:
        return False
    for cidr in allowed_cidrs:
        try:
            if address in ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def interface_details(name: str) -> dict[str, Any]:
    root = Path("/sys/class/net") / name
    details: dict[str, Any] = {
        "driver": "",
        "is_usb": False,
        "usb_vendor": "",
        "usb_product": "",
        "tether_kind": "",
    }
    try:
        device = (root / "device").resolve()
    except OSError:
        return details

    details["is_usb"] = "usb" in str(device)
    driver = root / "device" / "driver"
    try:
        details["driver"] = driver.resolve().name
    except OSError:
        details["driver"] = ""

    for parent in [device, *device.parents]:
        vendor = parent / "idVendor"
        product = parent / "idProduct"
        if vendor.exists():
            details["usb_vendor"] = _read_sysfs(vendor)
            details["usb_product"] = _read_sysfs(product)
            details["is_usb"] = True
            break

    details["tether_kind"] = tether_kind(details)
    return details


def tether_kind(details: dict[str, Any]) -> str:
    driver = str(details.get("driver", ""))
    vendor = str(details.get("usb_vendor", "")).lower()
    if driver == "ipheth" or vendor == "05ac":
        return "Apple USB tether"
    if driver == "rndis_host":
        return "Android USB tether"
    if driver in {"cdc_ether", "cdc_ncm"}:
        return "USB tether"
    if details.get("is_usb"):
        return "USB network"
    return ""


def _read_sysfs(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
