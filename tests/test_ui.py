import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.kiwoom_api import KiwoomAccountInfo
from kiwoom_auto_trader.ui import KiwoomRestLoginDialog, TraderApp


class UiHelperTests(unittest.TestCase):
    def test_reads_single_line_key_file_without_persisting_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account_appkey.txt"
            path.write_text("test-key-value\n", encoding="utf-8")

            value = KiwoomRestLoginDialog._read_key_file(path)

        self.assertEqual(value, "test-key-value")

    def test_rejects_multiline_key_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account_secretkey.txt"
            path.write_text("first\nsecond\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "형식"):
                KiwoomRestLoginDialog._read_key_file(path)

    def test_reads_matching_key_pair_from_download_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            (downloads / "account_appkey.txt").write_text("test-app-key\n", encoding="utf-8")
            (downloads / "account_secretkey.txt").write_text(
                "test-secret-key\n",
                encoding="utf-8",
            )

            key_pair = KiwoomRestLoginDialog._read_latest_key_pair(downloads)

        self.assertEqual(key_pair, ("test-app-key", "test-secret-key"))

    def test_does_not_pair_keys_with_different_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            (downloads / "first_appkey.txt").write_text("test-app-key\n", encoding="utf-8")
            (downloads / "second_secretkey.txt").write_text(
                "test-secret-key\n",
                encoding="utf-8",
            )

            key_pair = KiwoomRestLoginDialog._read_latest_key_pair(downloads)

        self.assertIsNone(key_pair)

    def test_labels_live_rest_account_as_order_locked(self):
        info = KiwoomAccountInfo(
            True,
            ["1234567890"],
            server_type="실거래",
            connection_method="REST API",
        )

        label = TraderApp._account_capability_label(info)

        self.assertIn("주문 잠금", label)


if __name__ == "__main__":
    unittest.main()
