import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.audio_alerts import TradeSoundNotifier


class TradeSoundNotifierTests(unittest.TestCase):
    def test_keeps_buy_and_sell_paths_separately(self):
        notifier = TradeSoundNotifier("buy.wav", "sell.wav")

        self.assertEqual(notifier.sound_path("BUY"), "buy.wav")
        self.assertEqual(notifier.sound_path("SELL"), "sell.wav")

    def test_missing_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "missing.wav")
            notifier = TradeSoundNotifier(path, "")

            self.assertFalse(notifier.play_execution("BUY"))


if __name__ == "__main__":
    unittest.main()
