import unittest
from unittest.mock import patch

from kiwoom_auto_trader.windows_integration import startup_command


class WindowsIntegrationTests(unittest.TestCase):
    def test_frozen_startup_command_quotes_executable(self):
        with patch("sys.executable", r"C:\Program Files\Kawaii\Kawaii.exe"), patch(
            "sys.frozen", True, create=True
        ):
            self.assertEqual(
                startup_command(),
                r'"C:\Program Files\Kawaii\Kawaii.exe"',
            )


if __name__ == "__main__":
    unittest.main()
