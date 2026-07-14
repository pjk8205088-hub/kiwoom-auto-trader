import unittest
from datetime import datetime

from kiwoom_auto_trader.charting import RealTimeCandleAggregator, moving_average
from kiwoom_auto_trader.models import Candle, RealTimeQuote


class ChartingTests(unittest.TestCase):
    def test_aggregates_official_realtime_trades_into_second_candles(self):
        aggregator = RealTimeCandleAggregator()
        now = datetime(2026, 7, 11, 10, 15, 30)

        aggregator.add(RealTimeQuote("005930", 100, volume=2, timestamp="101530"), now)
        aggregator.add(RealTimeQuote("005930", 103, volume=3, timestamp="101531"), now)
        aggregator.add(RealTimeQuote("005930", 99, volume=4, timestamp="101534"), now)
        aggregator.add(RealTimeQuote("005930", 105, volume=5, timestamp="101535"), now)

        one_second = aggregator.candles(1)
        five_second = aggregator.candles(5)

        self.assertEqual(len(one_second), 4)
        self.assertEqual(len(five_second), 2)
        self.assertEqual(five_second[0].open, 100)
        self.assertEqual(five_second[0].high, 103)
        self.assertEqual(five_second[0].low, 99)
        self.assertEqual(five_second[0].close, 99)
        self.assertEqual(five_second[0].volume, 9)

    def test_aggregates_official_one_tick_history_into_second_candles(self):
        aggregator = RealTimeCandleAggregator()
        now = datetime(2026, 7, 11, 10, 15, 30)

        aggregator.add_tick_candle(
            "005930",
            Candle(101, 99, 100, 100, 2, "20260711101530"),
            now,
        )
        aggregator.add_tick_candle(
            "005930",
            Candle(104, 102, 103, 103, 3, "20260711101530"),
            now,
        )

        candle = aggregator.candles(1)[0]
        self.assertEqual(candle.open, 100)
        self.assertEqual(candle.high, 104)
        self.assertEqual(candle.low, 99)
        self.assertEqual(candle.close, 103)
        self.assertEqual(candle.volume, 5)

    def test_calculates_moving_average_without_fabricating_early_values(self):
        candles = [
            Candle(high=value, low=value, close=value)
            for value in (10, 20, 30, 40, 50)
        ]

        values = moving_average(candles, 3)

        self.assertEqual(values[:2], [None, None])
        self.assertEqual(values[2:], [20, 30, 40])


if __name__ == "__main__":
    unittest.main()
