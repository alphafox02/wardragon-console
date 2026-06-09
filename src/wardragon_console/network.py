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

# Drivers that are unambiguously phone-tether protocols in the real world.
# RNDIS, CDC-NCM and ipheth are not used by any standard USB-Ethernet dongle;
# seeing one of these on an interface effectively guarantees a tethered phone.
UNAMBIGUOUS_PHONE_DRIVERS = frozenset({"rndis_host", "cdc_ncm", "ipheth"})

# USB vendor IDs of phone makers. Used to disambiguate the cdc_ether driver,
# which is shared between phones and generic USB-Ethernet adapters.
KNOWN_PHONE_VENDORS = frozenset({
    "05ac",  # Apple
    "04e8",  # Samsung
    "18d1",  # Google
    "2717",  # Xiaomi
    "2a70",  # OnePlus
    "12d1",  # Huawei
    "22b8",  # Motorola
    "1004",  # LG
})

# USB vendor IDs of USB-to-Ethernet dongle chipmakers. These are *never*
# phone tethers, regardless of which driver the kernel ends up binding or
# what subnet DHCP hands out. Checked before any tether-classification
# logic so a LAN dongle that happens to land in a default tether CIDR
# (rare but observed) never gets a console listener attached.
USB_ETHERNET_DONGLE_VENDORS = frozenset({
    "0bda",  # Realtek (RTL8152, RTL8153, RTL8156)
    "0b95",  # ASIX (AX88179, AX88772, AX88178)
    "13b1",  # Linksys
    "0846",  # NETGEAR
    "0411",  # Buffalo
    "1a40",  # Terminus / no-name USB hubs that bridge ethernet
    "9710",  # MosChip / Plugable
    "0fe6",  # ICS Advent / DM9601-based
})

# Subnets the console must never bind a tether listener on, even if the
# driver/vendor match. 172.31.100.0/24 is the AntSDR private link on a
# WarDragon kit.
NEVER_TETHER_PREFIXES = ("172.31.100.",)


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
    if any(ipv4.startswith(prefix) for prefix in NEVER_TETHER_PREFIXES):
        return False
    if not _is_private_ipv4(ipv4):
        return False

    driver = str(interface.get("driver", ""))
    vendor = str(interface.get("usb_vendor", "")).lower()

    # Known USB-Ethernet dongle vendors are never tethers, regardless of
    # driver or subnet. This is the primary defence against a LAN dongle
    # accidentally landing in a default tether CIDR and getting a listener
    # attached (which would expose the console on the LAN).
    if vendor in USB_ETHERNET_DONGLE_VENDORS:
        return False

    # Unambiguously phone-tether drivers: trust without the CIDR allowlist.
    # This is what fixes Samsung / Pixel / etc. on non-default tether subnets
    # like 10.x or anything outside 192.168.42|43.0/24.
    if driver in UNAMBIGUOUS_PHONE_DRIVERS:
        return True

    # Apple iPhone/iPad: ipheth covers most cases above; older paths report
    # only the USB vendor without a clean driver name.
    if vendor == "05ac":
        return True

    # cdc_ether is shared between phones and generic USB-Ethernet dongles.
    # Trust only when the USB vendor is a known phone maker. The previous
    # CIDR-only fallback was removed because a generic dongle with an IP
    # that happened to land in a default tether subnet would silently get
    # classified as a tether. If you have an exotic phone using cdc_ether
    # with an unrecognized vendor, add its VID to KNOWN_PHONE_VENDORS.
    if driver == "cdc_ether" and vendor in KNOWN_PHONE_VENDORS:
        return True
    return False


def _is_private_ipv4(ipv4: str) -> bool:
    try:
        address = ip_address(ipv4)
    except ValueError:
        return False
    return address.is_private and not address.is_loopback and not address.is_link_local


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
