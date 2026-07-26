from __future__ import annotations

from .models import Action, Candle, DmiPoint, PatternState, StrategySettings, TradeDecision


class StrategyEngine:
    def __init__(self, settings: StrategySettings | None = None) -> None:
        self.settings = settings or StrategySettings()
        self.previous_pattern: PatternState = "NONE"
        self.last_dmi: DmiPoint | None = None
        self._last_signal_candle_key = ""

    def reset(self) -> None:
        self.previous_pattern = "NONE"
        self.last_dmi = None
        self._last_signal_candle_key = ""

    def evaluate(self, candles: list[Candle]) -> TradeDecision:
        points = self.calculate_dmi_series(candles)
        if not points:
            self.last_dmi = None
            return TradeDecision(
                action="HOLD",
                reason=f"DMI({self.settings.dmi_period}일) 계산에 필요한 일봉이 부족합니다.",
                pattern_state="NONE",
            )

        current = points[-1]
        previous = points[-2] if len(points) >= 2 else None
        self.last_dmi = current
        self.previous_pattern = current.pattern_state
        candle_key = current.timestamp.strip() or (
            f"{len(candles)}:{candles[-1].high}:{candles[-1].low}:{candles[-1].close}"
        )
        if candle_key == self._last_signal_candle_key:
            return self._decision(
                "HOLD",
                "이미 판단한 일봉이라 중복 주문을 방지했습니다.",
                current,
            )
        if previous is not None:
            if previous.pattern_state == "BEARISH" and current.pattern_state == "BULLISH":
                self._last_signal_candle_key = candle_key
                return self._decision(
                    "BUY",
                    "DMI 약세에서 강세로 전환되어 매수 신호가 발생했습니다.",
                    current,
                )
            if previous.pattern_state == "BULLISH" and current.pattern_state == "BEARISH":
                self._last_signal_candle_key = candle_key
                return self._decision(
                    "SELL",
                    "DMI 강세에서 약세로 전환되어 매도 신호가 발생했습니다.",
                    current,
                )

        return self._decision("HOLD", "새로운 DMI 강세/약세 전환이 없습니다.", current)

    def calculate_dmi_series(self, candles: list[Candle]) -> list[DmiPoint]:
        period = self.settings.dmi_period
        if period <= 0:
            raise ValueError("dmi_period must be positive")
        if len(candles) <= period:
            return []

        true_ranges: list[float] = []
        plus_movements: list[float] = []
        minus_movements: list[float] = []
        for index in range(1, len(candles)):
            previous = candles[index - 1]
            current = candles[index]
            true_ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )
            upward = current.high - previous.high
            downward = previous.low - current.low
            plus_movements.append(upward if upward > downward and upward > 0 else 0.0)
            minus_movements.append(downward if downward > upward and downward > 0 else 0.0)

        smoothed_tr = sum(true_ranges[:period])
        smoothed_plus = sum(plus_movements[:period])
        smoothed_minus = sum(minus_movements[:period])
        dx_values: list[float] = []
        adx: float | None = None
        points: list[DmiPoint] = []

        for candle_index in range(period, len(candles)):
            movement_index = candle_index - 1
            if candle_index > period:
                smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[movement_index]
                smoothed_plus = (
                    smoothed_plus - (smoothed_plus / period) + plus_movements[movement_index]
                )
                smoothed_minus = (
                    smoothed_minus - (smoothed_minus / period) + minus_movements[movement_index]
                )

            if smoothed_tr > 0:
                plus_di = 100.0 * smoothed_plus / smoothed_tr
                minus_di = 100.0 * smoothed_minus / smoothed_tr
            else:
                plus_di = 0.0
                minus_di = 0.0
            total_di = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / total_di if total_di > 0 else 0.0
            dx_values.append(dx)
            if adx is None and len(dx_values) == period:
                adx = sum(dx_values) / period
            elif adx is not None:
                adx = ((adx * (period - 1)) + dx) / period

            pattern_state: PatternState = "NONE"
            if plus_di > minus_di:
                pattern_state = "BULLISH"
            elif minus_di > plus_di:
                pattern_state = "BEARISH"
            points.append(
                DmiPoint(
                    index=candle_index,
                    timestamp=candles[candle_index].timestamp,
                    plus_di=plus_di,
                    minus_di=minus_di,
                    adx=adx,
                    pattern_state=pattern_state,
                )
            )
        return points

    def latest_dmi(self, candles: list[Candle]) -> DmiPoint | None:
        points = self.calculate_dmi_series(candles)
        return points[-1] if points else None

    @staticmethod
    def _decision(action: Action, reason: str, point: DmiPoint) -> TradeDecision:
        return TradeDecision(
            action=action,
            reason=reason,
            pattern_state=point.pattern_state,
            dmi_plus=point.plus_di,
            dmi_minus=point.minus_di,
            adx=point.adx,
            candle_timestamp=point.timestamp,
        )
