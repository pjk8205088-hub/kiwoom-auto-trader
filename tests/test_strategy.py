import unittest

from kiwoom_auto_trader.models import Candle, StrategySettings
from kiwoom_auto_trader.strategy import StrategyEngine


class StrategyEngineTests(unittest.TestCase):
    def test_buy_on_bullish_entry(self):
        engine = StrategyEngine(StrategySettings(use_cci_filter=False))
        candles = [Candle(10, 8, 9) for _ in range(20)]

        decision = engine.evaluate(candles, "BULLISH")

        self.assertEqual(decision.action, "BUY")

    def test_sell_on_bullish_exit(self):
        engine = StrategyEngine(StrategySettings(use_cci_filter=False))
        candles = [Candle(10, 8, 9) for _ in range(20)]
        engine.evaluate(candles, "BULLISH")

        decision = engine.evaluate(candles, "BEARISH")

        self.assertEqual(decision.action, "SELL")

    def test_cci_returns_none_when_not_enough_candles(self):
        engine = StrategyEngine(StrategySettings(cci_period=20))

        self.assertIsNone(engine.calculate_cci([Candle(10, 8, 9)]))


if __name__ == "__main__":
    unittest.main()
