import unittest

from kiwoom_auto_trader.models import Candle, StrategySettings
from kiwoom_auto_trader.strategy import StrategyEngine
from kiwoom_auto_trader.strategy_profile_schema import new_profile


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
    def test_default_profile_stop_loss_overrides_unimplemented_normal_rules(self):
        profile = new_profile(1)
        candles = dmi_buy_transition_candles()

        decision = StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(
            candles, entry_price=10.0
        )

        self.assertEqual(decision.action, "SELL")
        self.assertIn("손실 제한", decision.reason)
        profile["sellEnabled"] = False
        blocked = StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(
            candles, entry_price=10.0
        )
        self.assertEqual(blocked.action, "HOLD")

    def test_target_profit_sells_only_with_known_entry_price(self):
        profile = new_profile(1)
        profile["sell"]["S03_dayTargetProfit"]["enabled"] = True
        candles = dmi_buy_transition_candles()

        matched = StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(
            candles, entry_price=7.0
        )
        unknown_entry = StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(candles)

        self.assertEqual(matched.action, "SELL")
        self.assertIn("목표 수익", matched.reason)
        self.assertEqual(unknown_entry.action, "HOLD")

    def test_stop_loss_rule_alone_does_not_sell_without_loss(self):
        profile = {
            "bar": {"combine": "MINUTE_ONLY", "minute": {"shortPeriod": 2, "longPeriod": 3}},
            "sell": {"S10_stopLoss": {"enabled": True, "pct": 3.5}},
        }
        decision = StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(
            dmi_buy_transition_candles(), entry_price=8.0
        )
        self.assertEqual(decision.action, "HOLD")

    def test_enabled_unimplemented_rule_blocks_profile_signal_and_dmi_fallback(self):
        profile = new_profile(1)
        engine = StrategyEngine(StrategySettings(dmi_period=3, profile=profile))

        decision = engine.evaluate(dmi_buy_transition_candles())

        self.assertEqual(decision.action, "HOLD")
        self.assertIn("미구현 설정", decision.reason)
        self.assertIn("X04_breakoutCooldown", decision.reason)

    def test_day_low_rebound_rule_requires_configured_rise(self):
        profile = {
            "bar": {"combine": "MINUTE_ONLY", "minute": {"shortPeriod": 2, "longPeriod": 3}},
            "buy": {"B03_fromDayLow": {"enabled": True, "risePct": 100.0}},
        }
        engine = StrategyEngine(StrategySettings(dmi_period=3, profile=profile))

        decision = engine.evaluate(dmi_buy_transition_candles())

        self.assertEqual(decision.action, "HOLD")
        profile["buy"]["B03_fromDayLow"]["risePct"] = 10.0
        matched = StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(
            dmi_buy_transition_candles()
        )
        self.assertEqual(matched.action, "BUY")

    def test_below_open_and_overextension_block_day_low_buy(self):
        candles = dmi_buy_transition_candles()
        candles[0] = Candle(high=10, low=8, close=9, open=9, timestamp=candles[0].timestamp)
        profile = {
            "bar": {"combine": "MINUTE_ONLY", "minute": {"shortPeriod": 2, "longPeriod": 3}},
            "buy": {"B03_fromDayLow": {"enabled": True, "risePct": 10.0}},
            "buyBlock": {"X01_belowDayOpen": {"enabled": True}},
        }
        settings = StrategySettings(dmi_period=3, profile=profile)
        self.assertEqual(StrategyEngine(settings).evaluate(candles).action, "HOLD")

        profile["buyBlock"] = {"X02_overExtended": {"enabled": True, "gapFromPrevClosePct": 5.0}}
        prior = [Candle(high=8, low=6, close=7, timestamp="20260710")]
        self.assertEqual(StrategyEngine(settings).evaluate(candles, prior).action, "HOLD")

    def test_open_breakout_requires_previous_minute_below_open(self):
        candles = dmi_buy_transition_candles()
        candles[0] = Candle(high=10, low=7, close=9, open=7.5, timestamp=candles[0].timestamp)
        profile = {
            "bar": {"combine": "MINUTE_ONLY", "minute": {"shortPeriod": 2, "longPeriod": 3}},
            "buy": {"B04_openBreakUp": {"enabled": True, "useMinute": True, "useDaily": True}},
        }

        self.assertEqual(
            StrategyEngine(StrategySettings(dmi_period=3, profile=profile)).evaluate(candles).action,
            "BUY",
        )

    def test_daily_closes_groups_compact_kiwoom_times_by_trading_day(self):
        candles = [
            Candle(high=11, low=9, close=10, timestamp="20260711090000"),
            Candle(high=12, low=10, close=11, timestamp="20260711120000"),
            Candle(high=14, low=12, close=13, timestamp="20260712090000"),
            Candle(high=15, low=13, close=14, timestamp="20260712150000"),
        ]

        self.assertEqual(StrategyEngine.daily_closes(candles), [11, 14])
        self.assertEqual(StrategyEngine.daily_moving_averages(candles, (2,)), {2: 12.5})

    def test_daily_history_is_extended_with_current_intraday_close(self):
        daily = [
            Candle(high=14, low=11, close=12, timestamp="20260713"),
            Candle(high=13, low=10, close=11, timestamp="20260712"),
            Candle(high=12, low=9, close=10, timestamp="20260711"),
        ]
        minute = [
            Candle(high=13, low=11, close=12, timestamp="20260713100000"),
            Candle(high=15, low=12, close=14, timestamp="20260713110000"),
        ]

        self.assertEqual(StrategyEngine.daily_closes_with_intraday(daily, minute), [10, 11, 14])

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
