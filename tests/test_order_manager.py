import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.broker import MockBroker
from kiwoom_auto_trader.order_manager import OrderManager
from kiwoom_auto_trader.storage import Storage


class OrderManagerTests(unittest.TestCase):
    def test_sell_retries_after_single_failure(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_test.sqlite3"
        if db.exists():
            db.unlink()
        broker = MockBroker()
        storage = Storage(db)
        manager = OrderManager(broker, storage, max_sell_attempts=3)
        manager.execute_buy("005930", 10, 72_000)
        broker.fail_next_sell = True

        result = manager.execute_sell("005930", 10, 72_100)

        self.assertTrue(result.success)
        self.assertEqual(broker.get_position("005930").quantity, 0)


if __name__ == "__main__":
    unittest.main()
