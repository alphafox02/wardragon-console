import unittest

from wardragon_console.actions import restart_dragonsync
from wardragon_console.settings import Settings


class ActionTests(unittest.TestCase):
    def test_restart_disallowed_on_remote_bind_by_default(self):
        settings = Settings(bind_host="0.0.0.0", allow_remote_config_write=True, allow_remote_restart=False)
        with self.assertRaises(PermissionError):
            restart_dragonsync(settings)

    def test_remote_config_write_does_not_imply_remote_restart(self):
        settings = Settings(bind_host="192.168.42.10", allow_remote_config_write=True, allow_remote_restart=False)
        self.assertTrue(settings.can_write_config())
        self.assertFalse(settings.can_restart_dragonsync())

    def test_restart_rejects_invalid_service_name(self):
        settings = Settings(bind_host="127.0.0.1", dragonsync_service_name="../dragonsync.service")
        with self.assertRaises(ValueError):
            restart_dragonsync(settings)


if __name__ == "__main__":
    unittest.main()
