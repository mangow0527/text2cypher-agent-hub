import unittest

from app.config import settings


class LoggingConfigTest(unittest.TestCase):
    def test_slow_request_threshold_has_default(self) -> None:
        self.assertEqual(settings.slow_request_threshold_ms, 5000)
