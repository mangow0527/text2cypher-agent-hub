import unittest

from app.config import settings


class ConfigTest(unittest.TestCase):
    def test_default_host_allows_lan_access(self) -> None:
        self.assertEqual(settings.host, "0.0.0.0")

    def test_settings_do_not_configure_qa_redispatch_timeout(self) -> None:
        self.assertFalse(hasattr(settings, "qa_agent_redispatch_timeout_seconds"))
