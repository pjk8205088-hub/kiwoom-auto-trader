import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from kiwoom_auto_trader.models import OrderResult, TradingBaseline
from kiwoom_auto_trader.storage import Storage


class StorageTests(unittest.TestCase):
    def test_persists_detailed_trade_history_and_csv_log(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "orders.sqlite3"
            csv_path = Path(directory) / "매수매도_이력.csv"
            storage = Storage(db_path, trade_history_path=csv_path)
            timestamp = datetime(2026, 7, 20, 9, 12, 34)

            storage.save_order_result(
                OrderResult(
                    "005930",
                    "BUY",
                    2,
                    72_000,
                    True,
                    "REST 실거래 매수주문 접수 완료",
                    timestamp,
                    symbol_name="삼성전자",
                    total_amount=144_000,
                    order_no="0000123",
                    order_mode="실거래",
                )
            )

            self.assertEqual(
                storage.recent_trade_history(1)[0],
                (
                    "2026-07-20T09:12:34",
                    "BUY",
                    "005930",
                    "삼성전자",
                    2,
                    72_000.0,
                    144_000.0,
                    1,
                    "0000123",
                    "실거래",
                    "REST 실거래 매수주문 접수 완료",
                ),
            )
            with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], list(Storage.TRADE_HISTORY_HEADERS))
            self.assertEqual(rows[1][1:7], ["매수", "005930", "삼성전자", "2", "72000", "144000"])
            self.assertEqual(rows[1][7:10], ["접수", "0000123", "실거래"])

    def test_migrates_existing_order_table_before_saving_detailed_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    create table orders (
                        id integer primary key autoincrement,
                        timestamp text not null,
                        symbol text not null,
                        side text not null,
                        quantity integer not null,
                        price real not null,
                        success integer not null,
                        message text not null
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            storage = Storage(db_path)
            connection = sqlite3.connect(db_path)
            try:
                columns = {row[1] for row in connection.execute("pragma table_info(orders)")}
            finally:
                connection.close()

            self.assertTrue(
                {"symbol_name", "total_amount", "order_no", "order_mode"}.issubset(columns)
            )

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

    def test_persists_watchlist_memo_and_migrates_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist-memo.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    create table watchlist (
                        id integer primary key autoincrement,
                        symbol text not null unique,
                        name text not null default '',
                        created_at text not null
                    )
                    """
                )
                connection.execute(
                    "insert into watchlist (symbol, name, created_at) values (?, ?, ?)",
                    ("005930", "삼성전자", "2026-08-01T10:00:00"),
                )
                connection.commit()
            finally:
                connection.close()

            storage = Storage(path)
            storage.set_watchlist_memo("005930", "장기 관찰 종목")

            reloaded = Storage(path)
            self.assertEqual(reloaded.watchlist_memo("005930"), "장기 관찰 종목")
            self.assertEqual(
                reloaded.watchlist_entries(),
                [("005930", "삼성전자", "장기 관찰 종목")],
            )

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

    def test_persists_updates_and_deletes_application_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.sqlite3"
            storage = Storage(path)
            storage.set_app_setting("profile.nickname", "이진솔")
            storage.set_app_setting("window.always_on_top", True)

            reloaded = Storage(path)
            self.assertEqual(reloaded.get_app_setting("profile.nickname"), "이진솔")
            self.assertEqual(reloaded.get_app_setting("window.always_on_top"), "True")
            self.assertEqual(reloaded.get_app_setting("missing", "fallback"), "fallback")

            reloaded.delete_app_setting("profile.nickname")
            self.assertEqual(reloaded.get_app_setting("profile.nickname"), "")

    def test_creates_consistent_exit_backup_with_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "data.sqlite3", trade_history_path=root / "orders.csv")
            storage.set_app_setting("profile.nickname", "테스트")
            storage.add_watchlist_symbol("005930", "삼성전자")
            storage.set_watchlist_memo("005930", "관심 메모")

            backup_dir = storage.create_backup(root / "backups")

            self.assertTrue((backup_dir / "data.sqlite3").is_file())
            self.assertTrue((backup_dir / "orders.csv").is_file())
            manifest = json.loads((backup_dir / "backup_manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["settings"]["profile.nickname"], "테스트")
            self.assertEqual(manifest["watchlist"][0]["memo"], "관심 메모")


if __name__ == "__main__":
    unittest.main()
