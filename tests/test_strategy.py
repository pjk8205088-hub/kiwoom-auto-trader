import unittest

from kiwoom_auto_trader.models import Candle, StrategySettings
from kiwoom_auto_trader.strategy import StrategyEngine


def dmi_buy_transition_candles() -> list[Candle]:
    return [
        Candle(high=10, low=8, close=9, timestamp="20260711090000"),
        Candle(high=9, low=7, close=8, timestamp="20260711090300"),
        Candle(high=8, low=6, close=7, timestamp="20260711090600"),
        Candle(high=7, low=5, close=6, timestamp="20260711090900"),
        Candle(high=8, low=6, close=7, timestamp="20260711091200"),
        Candle(high=9, low=7, close=8, timestamp="20260711091500"),
    ]


class StrategyEngineTests(unittest.TestCase):
    def test_calculates_wilder_dmi_and_adx(self):
        engine = StrategyEngine(StrategySettings(dmi_period=3))

        points = engine.calculate_dmi_series(dmi_buy_transition_candles())

        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[0].plus_di, 0.0, places=2)
        self.assertAlmostEqual(points[0].minus_di, 50.0, places=2)
        self.assertAlmostEqual(points[-1].plus_di, 27.7778, places=3)
        self.assertAlmostEqual(points[-1].minus_di, 22.2222, places=3)
        self.assertAlmostEqual(points[-1].adx, 48.1481, places=3)
        self.assertEqual(points[-1].pattern_state, "BULLISH")

    def test_buys_on_dmi_weak_to_strong_transition(self):
        engine = StrategyEngine(StrategySettings(dmi_period=3))

        decision = engine.evaluate(dmi_buy_transition_candles())

        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.pattern_state, "BULLISH")
        self.assertIn("약세에서 강세", decision.reason)

    def test_sells_on_dmi_strong_to_weak_transition(self):
        engine = StrategyEngine(StrategySettings(dmi_period=3))
        candles = dmi_buy_transition_candles() + [
            Candle(high=8, low=6, close=7, timestamp="20260711091800")
        ]

        decision = engine.evaluate(candles)

        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.pattern_state, "BEARISH")
        self.assertIn("강세에서 약세", decision.reason)

    def test_blocks_duplicate_decision_for_same_three_minute_candle(self):
        engine = StrategyEngine(StrategySettings(dmi_period=3))
        candles = dmi_buy_transition_candles()

        first = engine.evaluate(candles)
        duplicate = engine.evaluate(candles)

        self.assertEqual(first.action, "BUY")
        self.assertEqual(duplicate.action, "HOLD")
        self.assertIn("중복 주문", duplicate.reason)

    def test_hold_does_not_block_later_transition_in_same_candle(self):
        engine = StrategyEngine(StrategySettings(dmi_period=3))
        waiting = dmi_buy_transition_candles()[:-1]
        revised = waiting[:-1] + [
            Candle(high=10, low=8, close=9, timestamp=waiting[-1].timestamp)
        ]

        hold = engine.evaluate(waiting)
        buy = engine.evaluate(revised)

        self.assertEqual(hold.action, "HOLD")
        self.assertEqual(buy.action, "BUY")

    def test_holds_when_dmi_candles_are_insufficient(self):
        engine = StrategyEngine(StrategySettings(dmi_period=14))

        decision = engine.evaluate([Candle(high=10, low=8, close=9)])

        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.pattern_state, "NONE")
        self.assertIn("부족", decision.reason)


if __name__ == "__main__":
    unittest.main()
