from __future__ import annotations

from datetime import datetime

from .models import Candle, RealTimeQuote
from .symbols import normalize_symbol


SUPPORTED_SECOND_INTERVALS = (1, 5, 10)
SUPPORTED_MINUTE_INTERVALS = (1, 3, 5, 10, 15, 30, 45, 60)


def timeframe_label(timeframe: str) -> str:
    if timeframe.endswith("s"):
        return f"{int(timeframe[:-1])}초"
    minutes = int(timeframe[:-1])
    return "1시간" if minutes == 60 else f"{minutes}분"


def moving_average(candles: list[Candle], period: int) -> list[float | None]:
    period = max(1, int(period))
    values: list[float | None] = []
    total = 0.0
    closes: list[float] = []
    for candle in candles:
        closes.append(candle.close)
        total += candle.close
        if len(closes) > period:
            total -= closes[-period - 1]
        values.append(total / period if len(closes) >= period else None)
    return values


class RealTimeCandleAggregator:
    def __init__(self, max_candles: int = 600) -> None:
        self.max_candles = max(1, int(max_candles))
        self.symbol = ""
        self._candles: dict[int, list[Candle]] = {
            interval: [] for interval in SUPPORTED_SECOND_INTERVALS
        }

    def reset(self, symbol: str = "") -> None:
        self.symbol = normalize_symbol(symbol)
        self._candles = {interval: [] for interval in SUPPORTED_SECOND_INTERVALS}

    def add(self, quote: RealTimeQuote, now: datetime | None = None) -> None:
        symbol = normalize_symbol(quote.symbol)
        if not symbol or quote.current_price <= 0:
            return
        if self.symbol and symbol != self.symbol:
            self.reset(symbol)
        elif not self.symbol:
            self.symbol = symbol

        traded_at = self._quote_datetime(quote.timestamp, now)
        for interval in SUPPORTED_SECOND_INTERVALS:
            bucket_second = int(traded_at.timestamp())
            bucket_second -= bucket_second % interval
            bucket = datetime.fromtimestamp(bucket_second).strftime("%Y%m%d%H%M%S")
            self._merge(interval, bucket, quote.current_price, abs(int(quote.volume)))

    def candles(self, interval: int) -> list[Candle]:
        if interval not in SUPPORTED_SECOND_INTERVALS:
            raise ValueError(f"지원하지 않는 초봉 간격입니다: {interval}")
        return list(self._candles[interval])

    def _merge(self, interval: int, timestamp: str, price: float, volume: int) -> None:
        candles = self._candles[interval]
        existing_index = next(
            (index for index in range(len(candles) - 1, -1, -1) if candles[index].timestamp == timestamp),
            None,
        )
        if existing_index is None:
            candles.append(
                Candle(
                    high=price,
                    low=price,
                    close=price,
                    open=price,
                    volume=volume,
                    timestamp=timestamp,
                )
            )
            candles.sort(key=lambda candle: candle.timestamp)
        else:
            current = candles[existing_index]
            candles[existing_index] = Candle(
                high=max(current.high, price),
                low=min(current.low, price),
                close=price,
                open=current.open,
                volume=current.volume + volume,
                timestamp=timestamp,
            )
        if len(candles) > self.max_candles:
            del candles[: len(candles) - self.max_candles]

    @staticmethod
    def _quote_datetime(timestamp: str, now: datetime | None) -> datetime:
        current = now or datetime.now()
        digits = "".join(character for character in str(timestamp) if character.isdigit())
        if len(digits) >= 14:
            value = digits[:14]
        elif len(digits) >= 6:
            value = f"{current:%Y%m%d}{digits[-6:]}"
        else:
            return current.replace(microsecond=0)
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S")
        except ValueError:
            return current.replace(microsecond=0)
