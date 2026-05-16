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

    def test_phone_driver_on_normal_lan_subnet_is_not_tether_by_default(self):
        self.assertFalse(is_tether_interface({"name": "usb0", "ipv4": "192.168.68.68", "driver": "rndis_host"}))

    def test_custom_allowed_cidr_can_be_used(self):
        self.assertTrue(is_tether_interface(
            {"name": "usb0", "ipv4": "192.168.68.68", "driver": "rndis_host"},
            allowed_cidrs=("192.168.68.0/24",),
        ))


if __name__ == "__main__":
    unittest.main()
