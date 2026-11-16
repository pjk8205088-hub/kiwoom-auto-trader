from __future__ import annotations

from datetime import datetime

from .models import Action, Candle, DmiPoint, PatternState, StrategySettings, TradeDecision


class StrategyEngine:
    # Every daily period from 2 to 60 is available to the strategy layer.
    DAILY_DETAIL_PERIODS = tuple(range(2, 61))

    def __init__(self, settings: StrategySettings | None = None) -> None:
        self.settings = settings or StrategySettings()
        self.previous_pattern: PatternState = "NONE"
        self.last_dmi: DmiPoint | None = None
        self._last_signal_candle_key = ""

    def reset(self) -> None:
        self.previous_pattern = "NONE"
        self.last_dmi = None
        self._last_signal_candle_key = ""

    def evaluate(
        self, candles: list[Candle], daily_candles: list[Candle] | None = None,
        entry_price: float = 0.0,
    ) -> TradeDecision:
        points = self.calculate_dmi_series(candles)
        if not points:
            self.last_dmi = None
            return TradeDecision(
                action="HOLD",
                reason=f"DMI({self.settings.dmi_period}) 계산에 필요한 3분봉이 부족합니다.",
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
                "이미 판단한 3분봉이라 중복 주문을 방지했습니다.",
                current,
            )
        profile_signal = self._profile_signal(candles, daily_candles, entry_price)
        if profile_signal is not None:
            action, reason = profile_signal
            if action != "HOLD":
                self._last_signal_candle_key = candle_key
            return self._decision(action, reason, current)
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

    def _profile_signal(
        self, candles: list[Candle], daily_candles: list[Candle] | None = None,
        entry_price: float = 0.0,
    ) -> tuple[Action, str] | None:
        """Apply the selected saved profile without changing the DMI fallback."""
        profile = self.settings.profile
        if not isinstance(profile, dict):
            return None
        sell = profile.get("sell", {})
        if profile.get("sellEnabled", True) and entry_price > 0 and candles:
            profit_pct = (candles[-1].close / entry_price - 1) * 100
            stop_loss = sell.get("S10_stopLoss", {})
            if stop_loss.get("enabled") and profit_pct <= -float(stop_loss.get("pct", 3.5)):
                return "SELL", f"손실 제한 매도: 평균가 대비 {profit_pct:.2f}%"
            target = sell.get("S03_dayTargetProfit", {})
            if target.get("enabled") and profit_pct >= float(target.get("targetPct", 3.0)):
                return "SELL", f"목표 수익 매도: 평균가 대비 {profit_pct:.2f}%"
        unsupported = self.unsupported_profile_rules(profile)
        if unsupported:
            return "HOLD", "미구현 설정이 켜져 있어 자동주문을 차단했습니다: " + ", ".join(unsupported)
        bar = profile.get("bar", {})
        buy_enabled = bool(profile.get("buyEnabled", True))
        sell_enabled = bool(profile.get("sellEnabled", True))
        minute = bar.get("minute", {})
        buy = profile.get("buy", {})
        buy_block = profile.get("buyBlock", {})
        sell = profile.get("sell", {})
        sell_block = profile.get("sellBlock", {})
        short_enabled = bool(minute.get("shortEnabled", True))
        long_enabled = bool(minute.get("longEnabled", True))
        short_period = max(1, int(minute.get("shortPeriod", 5)))
        long_period = max(short_period + 1, int(minute.get("longPeriod", 20)))
        required = short_period if not long_enabled else long_period
        if len(candles) < required + 1:
            return "HOLD", f"이평선 계산에 필요한 분봉이 부족합니다({len(candles)}/{required + 1})."

        def average(end: int, period: int) -> float:
            values = [c.close for c in candles[end - period:end]]
            return sum(values) / period

        current_short = average(len(candles), short_period) if short_enabled else 0.0
        previous_short = average(len(candles) - 1, short_period) if short_enabled else 0.0
        current_long = average(len(candles), long_period) if long_enabled else current_short
        previous_long = average(len(candles) - 1, long_period) if long_enabled else previous_short
        crossed_up = current_short > current_long and previous_short <= previous_long
        crossed_down = current_short < current_long and previous_short >= previous_long
        previous_close = candles[-2].close
        current_close = candles[-1].close
        single_cross_up = previous_close <= previous_short and current_close > current_short
        single_cross_down = previous_close >= previous_short and current_close < current_short
        bullish = candles[-1].close > candles[-1].open
        bearish = candles[-1].close < candles[-1].open
        current_day = self._day_key(candles[-1].timestamp, len(candles) - 1)
        today_candles = [
            candle for index, candle in enumerate(candles)
            if self._day_key(candle.timestamp, index) == current_day
        ]
        day_open = today_candles[0].open if today_candles else 0.0
        day_low = min((candle.low for candle in today_candles if candle.low > 0), default=0.0)
        if buy_block.get("X01_belowDayOpen", {}).get("enabled") and day_open <= 0:
            return "HOLD", "시초가 아래 매수 제한을 확인할 당일 시가가 없습니다."
        if buy_block.get("X02_overExtended", {}).get("enabled"):
            prior = [
                candle for index, candle in enumerate(daily_candles or [])
                if self._day_key(candle.timestamp, index) < current_day and candle.close > 0
            ]
            if not prior:
                return "HOLD", "단기 과열 매수 제한을 확인할 전일 종가가 없습니다."
            previous_day_close = max(
                prior, key=lambda candle: self._day_key(candle.timestamp, 0)
            ).close
        else:
            previous_day_close = 0.0
        buy_rules = [value for key, value in buy.items() if isinstance(value, dict) and value.get("enabled")]
        sell_rules = [
            value for key, value in sell.items()
            if key in {"S01_maTurnDown", "S02_minuteCross"}
            and isinstance(value, dict) and value.get("enabled")
        ]
        buy_blocked = bool(
            buy_block.get("X03_maFalling", {}).get("enabled")
            and current_short < previous_short
        )
        buy_blocked = buy_blocked or bool(
            buy_block.get("X01_belowDayOpen", {}).get("enabled") and current_close < day_open
        )
        buy_blocked = buy_blocked or bool(
            buy_block.get("X02_overExtended", {}).get("enabled")
            and (current_close / previous_day_close - 1) * 100
            > float(buy_block["X02_overExtended"].get("gapFromPrevClosePct", 5.0))
        )
        sell_blocked = bool(
            sell_block.get("Y03_maRising", {}).get("enabled")
            and current_short > previous_short
        )
        buy_cross_rule = bool(buy.get("B02_minuteCross", {}).get("enabled"))
        sell_cross_rule = bool(sell.get("S02_minuteCross", {}).get("enabled"))
        buy_ma_rule = bool(buy.get("B01_maUpBullish", {}).get("enabled"))
        sell_ma_rule = bool(sell.get("S01_maTurnDown", {}).get("enabled"))
        buy_ok = bool(buy_rules) and not buy_blocked
        sell_ok = bool(sell_rules) and not sell_blocked
        buy_ok = buy_ok and buy_enabled
        sell_ok = sell_ok and sell_enabled
        if buy_ma_rule:
            buy_ok = buy_ok and current_short >= previous_short and bullish
        if sell_ma_rule:
            sell_ok = sell_ok and current_short <= previous_short and bearish
        if buy_cross_rule:
            buy_ok = buy_ok and (crossed_up if long_enabled else single_cross_up)
        if buy.get("B03_fromDayLow", {}).get("enabled"):
            if day_low <= 0:
                return "HOLD", "당일 저점 반등 매수를 확인할 저가가 없습니다."
            buy_ok = buy_ok and (current_close / day_low - 1) * 100 >= float(
                buy["B03_fromDayLow"].get("risePct", 1.0)
            )
        if buy.get("B04_openBreakUp", {}).get("enabled"):
            if day_open <= 0:
                return "HOLD", "시초가 상향 돌파 매수를 확인할 시가가 없습니다."
            rule = buy["B04_openBreakUp"]
            if not rule.get("useMinute", True) and not rule.get("useDaily", True):
                return "HOLD", "시초가 상향 돌파 매수의 확인 주기가 꺼져 있습니다."
            if rule.get("useMinute", True):
                buy_ok = buy_ok and previous_close <= day_open < current_close
            if rule.get("useDaily", True):
                buy_ok = buy_ok and current_close > day_open
        if sell_cross_rule:
            sell_ok = sell_ok and (crossed_down if long_enabled else single_cross_down)

        combine = str(bar.get("combine", "BOTH"))
        daily = bar.get("daily", {})
        if combine in {"BOTH", "DAILY_ONLY"}:
            daily_closes = self.daily_closes_with_intraday(daily_candles or [], candles)
            daily_short_enabled = bool(daily.get("shortEnabled", True))
            daily_long_enabled = bool(daily.get("longEnabled", True))
            daily_short_period = min(60, max(2, int(daily.get("shortPeriod", 5))))
            daily_long_period = min(60, max(daily_short_period + 1, int(daily.get("longPeriod", 20))))
            daily_required = daily_short_period if not daily_long_enabled else daily_long_period
            if len(daily_closes) < daily_required + 1:
                return "HOLD", f"일자별 이평선 계산에 필요한 거래일이 부족합니다({len(daily_closes)}/{daily_required + 1})."

            def daily_average(end: int, period: int) -> float:
                return sum(daily_closes[end - period:end]) / period

            ds = daily_average(len(daily_closes), daily_short_period) if daily_short_enabled else 0.0
            pds = daily_average(len(daily_closes) - 1, daily_short_period) if daily_short_enabled else 0.0
            dl = daily_average(len(daily_closes), daily_long_period) if daily_long_enabled else ds
            pdl = daily_average(len(daily_closes) - 1, daily_long_period) if daily_long_enabled else pds
            daily_up = ds > dl and pds <= pdl
            daily_down = ds < dl and pds >= pdl
            daily_bull = daily_closes[-1] >= daily_closes[-2]
            daily_bear = daily_closes[-1] <= daily_closes[-2]
            daily_single_up = daily_closes[-2] <= pds and daily_closes[-1] > ds
            daily_single_down = daily_closes[-2] >= pds and daily_closes[-1] < ds

            if combine == "DAILY_ONLY":
                buy_ok = bool(buy_rules) and not buy_blocked and buy_enabled
                sell_ok = bool(sell_rules) and not sell_blocked and sell_enabled
            if buy_ma_rule:
                buy_ok = buy_ok and daily_bull
            if sell_ma_rule:
                sell_ok = sell_ok and daily_bear
            if buy_cross_rule:
                buy_ok = buy_ok and (daily_up if daily_long_enabled else daily_single_up)
            if sell_cross_rule:
                sell_ok = sell_ok and (daily_down if daily_long_enabled else daily_single_down)

        if buy_ok:
            return "BUY", f"설정 이평선 매수 조건 충족: 단기 {current_short:.2f} / 장기 {current_long:.2f}"
        if sell_ok:
            return "SELL", f"설정 이평선 매도 조건 충족: 단기 {current_short:.2f} / 장기 {current_long:.2f}"
        return "HOLD", "설정한 이평선·캔들 조건이 아직 충족되지 않았습니다."

    @staticmethod
    def unsupported_profile_rules(profile: dict) -> list[str]:
        implemented = {
            "buy": {"B01_maUpBullish", "B02_minuteCross", "B03_fromDayLow", "B04_openBreakUp"},
            "buyBlock": {"X01_belowDayOpen", "X02_overExtended", "X03_maFalling"},
            "sell": {"S01_maTurnDown", "S02_minuteCross", "S03_dayTargetProfit", "S10_stopLoss"},
            "sellBlock": {"Y03_maRising"},
        }
        return [
            f"{group}.{key}"
            for group, supported in implemented.items()
            for key, value in profile.get(group, {}).items()
            if isinstance(value, dict) and value.get("enabled") and key not in supported
        ]

    @classmethod
    def daily_closes(cls, candles: list[Candle]) -> list[float]:
        """Return the latest close for each trading day in candle order."""
        closes: list[float] = []
        days: list[str] = []
        for index, candle in enumerate(candles):
            day = cls._day_key(candle.timestamp, index)
            if days and day == days[-1]:
                closes[-1] = candle.close
            else:
                days.append(day)
                closes.append(candle.close)
        return closes

    @classmethod
    def daily_closes_with_intraday(
        cls, daily_candles: list[Candle], minute_candles: list[Candle]
    ) -> list[float]:
        by_day = {
            cls._day_key(candle.timestamp, index): candle.close
            for index, candle in enumerate(reversed(daily_candles))
            if candle.close > 0
        }
        for index, candle in enumerate(minute_candles):
            if candle.close > 0:
                by_day[cls._day_key(candle.timestamp, index)] = candle.close
        return [by_day[day] for day in sorted(by_day)]

    @classmethod
    def daily_moving_averages(
        cls, candles: list[Candle], periods: tuple[int, ...] | None = None
    ) -> dict[int, float]:
        """Calculate selectable 2-60 day MAs from intraday candle data."""
        closes = cls.daily_closes(candles)
        selected = periods or cls.DAILY_DETAIL_PERIODS
        return {
            period: sum(closes[-period:]) / period
            for period in selected
            if period >= 1 and len(closes) >= period
        }

    @staticmethod
    def _day_key(timestamp: str, fallback_index: int) -> str:
        raw = (timestamp or "").strip()
        if raw:
            try:
                if len(raw) >= 8 and raw[:8].isdigit():
                    return datetime.strptime(raw[:8], "%Y%m%d").date().isoformat()
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                if len(raw) >= 10:
                    return raw[:10]
        return f"candle-{fallback_index}"

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
