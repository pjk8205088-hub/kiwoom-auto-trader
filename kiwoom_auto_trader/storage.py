from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import OrderResult, SystemLog, TradingBaseline


class Storage:
    TRADE_HISTORY_HEADERS = (
        "시간",
        "구분",
        "종목번호",
        "종목명",
        "수량",
        "요청시세",
        "예상총액",
        "결과",
        "주문번호",
        "거래환경",
        "메시지",
    )

    def __init__(
        self,
        db_path: str | Path = "kiwoom_auto_trader.sqlite3",
        trade_history_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.trade_history_path = (
            Path(trade_history_path)
            if trade_history_path is not None
            else self.db_path.with_name(f"{self.db_path.stem}_매수매도_이력.csv")
        )
        self.trade_history_file_error = ""
        self._init_db()
        self._ensure_trade_history_file()

    def save_order_result(self, result: OrderResult) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                insert into orders
                    (timestamp, symbol, side, quantity, price, success, message,
                     symbol_name, total_amount, order_no, order_mode)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.timestamp.isoformat(timespec="seconds"),
                    result.symbol,
                    result.side,
                    result.quantity,
                    result.price,
                    int(result.success),
                    result.message,
                    result.symbol_name,
                    result.total_amount,
                    result.order_no,
                    result.order_mode,
                ),
            )
        self._append_trade_history_file(result)

    def save_log(self, log: SystemLog) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                insert into logs (timestamp, level, category, message)
                values (?, ?, ?, ?)
                """,
                (
                    log.timestamp.isoformat(timespec="seconds"),
                    log.level,
                    log.category,
                    log.message,
                ),
            )

    def log(self, level: str, category: str, message: str) -> None:
        self.save_log(
            SystemLog(
                level=level,  # type: ignore[arg-type]
                category=category,
                message=message,
                timestamp=datetime.now(),
            )
        )

    def recent_orders(self, limit: int = 20) -> list[tuple]:
        with self._connection() as conn:
            return list(
                conn.execute(
                    """
                    select timestamp, symbol, side, quantity, price, success, message
                    from orders
                    order by id desc
                    limit ?
                    """,
                    (limit,),
                )
            )

    def recent_trade_history(self, limit: int = 200) -> list[tuple]:
        with self._connection() as conn:
            return list(
                conn.execute(
                    """
                    select timestamp, side, symbol, symbol_name, quantity, price,
                           total_amount, success, order_no, order_mode, message
                    from orders
                    order by id desc
                    limit ?
                    """,
                    (limit,),
                )
            )

    def recent_logs(self, limit: int = 20) -> list[tuple]:
        with self._connection() as conn:
            return list(
                conn.execute(
                    """
                    select timestamp, level, category, message
                    from logs
                    order by id desc
                    limit ?
                    """,
                    (limit,),
                )
            )

    def add_watchlist_symbol(self, symbol: str, name: str = "") -> None:
        with self._connection() as conn:
            conn.execute(
                """
                insert or ignore into watchlist (symbol, name, created_at)
                values (?, ?, ?)
                """,
                (symbol, name, datetime.now().isoformat(timespec="seconds")),
            )
            if name:
                conn.execute(
                    "update watchlist set name = ? where symbol = ?",
                    (name, symbol),
                )

    def remove_watchlist_symbol(self, symbol: str) -> None:
        with self._connection() as conn:
            conn.execute("delete from watchlist where symbol = ?", (symbol,))

    def watchlist_symbols(self) -> list[tuple[str, str]]:
        with self._connection() as conn:
            return list(
                conn.execute(
                    "select symbol, name from watchlist order by id",
                )
            )

    def save_trading_baseline(self, baseline: TradingBaseline) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                insert into trading_baselines
                    (symbol, capital_limit, reference_price, set_at)
                values (?, ?, ?, ?)
                on conflict(symbol) do update set
                    capital_limit = excluded.capital_limit,
                    reference_price = excluded.reference_price,
                    set_at = excluded.set_at
                """,
                (
                    baseline.symbol,
                    baseline.capital_limit,
                    baseline.reference_price,
                    baseline.set_at,
                ),
            )

    def trading_baseline(self, symbol: str) -> TradingBaseline | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                select symbol, capital_limit, reference_price, set_at
                from trading_baselines
                where symbol = ?
                """,
                (symbol,),
            ).fetchone()
        return TradingBaseline(*row) if row else None

    def remove_trading_baseline(self, symbol: str) -> None:
        with self._connection() as conn:
            conn.execute("delete from trading_baselines where symbol = ?", (symbol,))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                create table if not exists orders (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    symbol text not null,
                    side text not null,
                    quantity integer not null,
                    price real not null,
                    success integer not null,
                    message text not null,
                    symbol_name text not null default '',
                    total_amount real not null default 0,
                    order_no text not null default '',
                    order_mode text not null default ''
                )
                """
            )
            self._ensure_order_columns(conn)
            conn.execute(
                """
                create table if not exists logs (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    level text not null,
                    category text not null,
                    message text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists watchlist (
                    id integer primary key autoincrement,
                    symbol text not null unique,
                    name text not null default '',
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists trading_baselines (
                    symbol text primary key,
                    capital_limit real not null,
                    reference_price real not null,
                    set_at text not null
                )
                """
            )

    @staticmethod
    def _ensure_order_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row[1])
            for row in conn.execute("pragma table_info(orders)")
        }
        additions = {
            "symbol_name": "text not null default ''",
            "total_amount": "real not null default 0",
            "order_no": "text not null default ''",
            "order_mode": "text not null default ''",
        }
        for name, definition in additions.items():
            if name not in existing:
                conn.execute(f"alter table orders add column {name} {definition}")

    def _ensure_trade_history_file(self) -> None:
        try:
            self.trade_history_path.parent.mkdir(parents=True, exist_ok=True)
            if self.trade_history_path.exists() and self.trade_history_path.stat().st_size > 0:
                return
            with self.trade_history_path.open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerow(self.TRADE_HISTORY_HEADERS)
            self.trade_history_file_error = ""
        except OSError as exc:
            self.trade_history_file_error = str(exc)

    def _append_trade_history_file(self, result: OrderResult) -> None:
        self._ensure_trade_history_file()
        if self.trade_history_file_error:
            return
        try:
            with self.trade_history_path.open("a", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow(
                    (
                        result.timestamp.isoformat(timespec="seconds"),
                        "매수" if result.side == "BUY" else "매도",
                        result.symbol,
                        result.symbol_name,
                        result.quantity,
                        f"{result.price:.0f}",
                        f"{result.total_amount:.0f}",
                        "접수" if result.success else "실패",
                        result.order_no,
                        result.order_mode,
                        result.message,
                    )
                )
            self.trade_history_file_error = ""
        except OSError as exc:
            self.trade_history_file_error = str(exc)
