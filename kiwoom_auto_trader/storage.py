from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import OrderResult, SystemLog


class Storage:
    def __init__(self, db_path: str | Path = "kiwoom_auto_trader.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def save_order_result(self, result: OrderResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into orders
                    (timestamp, symbol, side, quantity, price, success, message)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.timestamp.isoformat(timespec="seconds"),
                    result.symbol,
                    result.side,
                    result.quantity,
                    result.price,
                    int(result.success),
                    result.message,
                ),
            )

    def save_log(self, log: SystemLog) -> None:
        with self._connect() as conn:
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
        with self._connect() as conn:
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

    def recent_logs(self, limit: int = 20) -> list[tuple]:
        with self._connect() as conn:
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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
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
                    message text not null
                )
                """
            )
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
