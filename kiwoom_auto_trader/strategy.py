from __future__ import annotations

from .models import Candle, PatternState, StrategySettings, TradeDecision


class StrategyEngine:
    def __init__(self, settings: StrategySettings | None = None) -> None:
        self.settings = settings or StrategySettings()
        self.previous_pattern: PatternState = "NONE"

    def evaluate(self, candles: list[Candle], pattern_state: PatternState) -> TradeDecision:
        cci = self.calculate_cci(candles)
        previous = self.previous_pattern
        self.previous_pattern = pattern_state

        entered_bullish = pattern_state == "BULLISH" and previous != "BULLISH"
        exited_bullish = previous == "BULLISH" and pattern_state != "BULLISH"

        if entered_bullish:
            if self._buy_blocked_by_cci(cci):
                return TradeDecision(
                    "HOLD",
                    "Bullish entry ignored because CCI is above the upper limit.",
                    cci,
                    pattern_state,
                )
            return TradeDecision("BUY", "Bullish pattern entry.", cci, pattern_state)

        if exited_bullish:
            return TradeDecision("SELL", "Bullish pattern exit.", cci, pattern_state)

        return TradeDecision("HOLD", "No actionable pattern transition.", cci, pattern_state)

    def calculate_cci(self, candles: list[Candle]) -> float | None:
        period = self.settings.cci_period
        if period <= 0:
            raise ValueError("cci_period must be positive")
        if len(candles) < period:
            return None

        sample = candles[-period:]
        typical_prices = [(c.high + c.low + c.close) / 3.0 for c in sample]
        average = sum(typical_prices) / period
        mean_deviation = sum(abs(price - average) for price in typical_prices) / period
        if mean_deviation == 0:
            return 0.0
        return (typical_prices[-1] - average) / (0.015 * mean_deviation)

    def _buy_blocked_by_cci(self, cci: float | None) -> bool:
        if not self.settings.use_cci_filter or cci is None:
            return False
        return cci >= self.settings.cci_upper
