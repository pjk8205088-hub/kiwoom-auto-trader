import tempfile
import unittest
from pathlib import Path

from kiwoom_auto_trader.models import StrategySettings
from kiwoom_auto_trader.service import AutoTradingService
from kiwoom_auto_trader.storage import Storage


class AutoTradingServiceTests(unittest.TestCase):
    def test_configure_preserves_strategy_state_when_settings_do_not_change(self):
        db = Path(tempfile.gettempdir()) / "kiwoom_auto_trader_service_test.sqlite3"
        if db.exists():
            db.unlink()
        service = AutoTradingService(storage=Storage(db))
        settings = StrategySettings(use_cci_filter=False)

        service.configure("005930", 1_000_000, settings)
        service.strategy.previous_pattern = "BULLISH"
        service.configure("005930", 1_000_000, settings)

        self.assertEqual(service.strategy.previous_pattern, "BULLISH")


if __name__ == "__main__":
    unittest.main()
