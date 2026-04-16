import unittest

from app.config import settings


class ConfigTest(unittest.TestCase):
    def test_default_host_allows_lan_access(self) -> None:
        self.assertEqual(settings.host, "0.0.0.0")
