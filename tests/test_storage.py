import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
