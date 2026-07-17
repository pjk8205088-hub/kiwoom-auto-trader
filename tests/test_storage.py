import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.models import TradingBaseline
from kiwoom_auto_trader.storage import Storage


class StorageTests(unittest.TestCase):
    def test_persists_updates_and_removes_watchlist_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.sqlite3"
            storage = Storage(path)
            storage.add_watchlist_symbol("005930", "삼성전자")
            storage.add_watchlist_symbol("000660", "")
            storage.add_watchlist_symbol("000660", "SK하이닉스")

            reloaded = Storage(path)
            self.assertEqual(
                reloaded.watchlist_symbols(),
                [("005930", "삼성전자"), ("000660", "SK하이닉스")],
            )

            reloaded.remove_watchlist_symbol("005930")
            self.assertEqual(reloaded.watchlist_symbols(), [("000660", "SK하이닉스")])

    def test_persists_updates_and_resets_trading_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.sqlite3"
            storage = Storage(path)
            storage.save_trading_baseline(
                TradingBaseline("005930", 500_000, 72_000, "2026-07-17T09:10:11")
            )

            reloaded = Storage(path)
            self.assertEqual(
                reloaded.trading_baseline("005930"),
                TradingBaseline("005930", 500_000, 72_000, "2026-07-17T09:10:11"),
            )

            reloaded.save_trading_baseline(
                TradingBaseline("005930", 600_000, 73_000, "2026-07-18T10:11:12")
            )
            self.assertEqual(reloaded.trading_baseline("005930").capital_limit, 600_000)

            reloaded.remove_trading_baseline("005930")
            self.assertIsNone(reloaded.trading_baseline("005930"))


if __name__ == "__main__":
    unittest.main()
