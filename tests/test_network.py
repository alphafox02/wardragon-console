import unittest

from wardragon_console.network import is_tether_interface, tether_kind


class NetworkDetectionTests(unittest.TestCase):
    def test_android_rndis_driver_is_tether_candidate(self):
        interface = {"name": "usb0", "ipv4": "192.168.42.23", "driver": "rndis_host"}
        self.assertTrue(is_tether_interface(interface))
        self.assertEqual(tether_kind(interface), "Android USB tether")

    def test_apple_vendor_is_tether_candidate(self):
        interface = {"name": "enx123", "ipv4": "172.20.10.2", "driver": "", "usb_vendor": "05ac"}
        self.assertTrue(is_tether_interface(interface))
        self.assertEqual(tether_kind(interface), "Apple USB tether")

    def test_link_local_and_wifi_are_not_tether_candidates(self):
        self.assertFalse(is_tether_interface({"name": "usb0", "ipv4": "169.254.1.2", "driver": "rndis_host"}))
        self.assertFalse(is_tether_interface({"name": "wlan0", "ipv4": "192.168.1.20", "driver": "iwlwifi"}))
        self.assertFalse(is_tether_interface({
            "name": "enx00e04c364189",
            "ipv4": "192.168.68.68",
            "driver": "r8152",
            "is_usb": True,
            "usb_vendor": "0bda",
        }))

    def test_unambiguous_phone_driver_is_tether_even_outside_default_cidrs(self):
        # rndis_host is never used by USB-Ethernet dongles, so any private IP
        # is good. Samsung tethers commonly land on 10.x.
        interface = {"name": "enxd66e", "ipv4": "10.152.47.95", "driver": "rndis_host", "usb_vendor": "04e8"}
        self.assertTrue(is_tether_interface(interface))

    def test_cdc_ether_requires_known_phone_vendor_or_cidr_match(self):
        # cdc_ether is ambiguous (dongles use it too). Plain Realtek dongle
        # with cdc_ether driver should be rejected outside the allowed CIDRs.
        dongle = {"name": "enx00e0", "ipv4": "192.168.68.5", "driver": "cdc_ether", "usb_vendor": "0bda"}
        self.assertFalse(is_tether_interface(dongle))
        # Same driver + known phone vendor: tether.
        phone = {"name": "enxabcd", "ipv4": "10.10.10.5", "driver": "cdc_ether", "usb_vendor": "18d1"}
        self.assertTrue(is_tether_interface(phone))

    def test_antsdr_link_is_never_tether(self):
        # The kit's built-in LAN port is statically 172.31.100.1/24 for
        # AntSDR. Even if a fake driver were reported, we never bind there.
        self.assertFalse(is_tether_interface({"name": "enp1s0", "ipv4": "172.31.100.1", "driver": "rndis_host"}))

    def test_custom_allowed_cidr_lets_cdc_ether_through(self):
        # The CIDR override is still useful for the ambiguous cdc_ether case
        # where the operator wants to whitelist a known good subnet.
        self.assertTrue(is_tether_interface(
            {"name": "usb0", "ipv4": "192.168.68.68", "driver": "cdc_ether", "usb_vendor": "0bda"},
            allowed_cidrs=("192.168.68.0/24",),
        ))


if __name__ == "__main__":
    unittest.main()
