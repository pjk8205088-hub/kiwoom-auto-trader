from __future__ import annotations

import csv
import json
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

    def watchlist_entries(self) -> list[tuple[str, str, str]]:
        with self._connection() as conn:
            return list(
                conn.execute(
                    "select symbol, name, memo from watchlist order by id",
                )
            )

    def watchlist_memo(self, symbol: str) -> str:
        with self._connection() as conn:
            row = conn.execute(
                "select memo from watchlist where symbol = ?",
                (str(symbol or "").strip(),),
            ).fetchone()
        return str(row[0]) if row else ""

    def set_watchlist_memo(self, symbol: str, memo: str) -> None:
        normalized = str(symbol or "").strip()
        if not normalized:
            raise ValueError("종목코드를 입력해 주세요.")
        cleaned = str(memo or "").strip()[:2000]
        with self._connection() as conn:
            conn.execute(
                "update watchlist set memo = ? where symbol = ?",
                (cleaned, normalized),
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

    def set_app_setting(self, key: str, value: object) -> None:
        setting_key = str(key or "").strip()
        if not setting_key:
            raise ValueError("설정 키는 비워 둘 수 없습니다.")
        with self._connection() as conn:
            conn.execute(
                """
                insert into app_settings (key, value, updated_at)
                values (?, ?, ?)
                on conflict(key) do update set
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    setting_key,
                    str(value),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def get_app_setting(self, key: str, default: str = "") -> str:
        with self._connection() as conn:
            row = conn.execute(
                "select value from app_settings where key = ?",
                (str(key or "").strip(),),
            ).fetchone()
        return str(row[0]) if row else default

    def delete_app_setting(self, key: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "delete from app_settings where key = ?",
                (str(key or "").strip(),),
            )

    def all_app_settings(self) -> dict[str, str]:
        with self._connection() as conn:
            rows = conn.execute(
                "select key, value from app_settings order by key"
            ).fetchall()
        return {str(key): str(value) for key, value in rows}

    def create_backup(self, destination_root: str | Path | None = None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = (
            Path(destination_root)
            if destination_root is not None
            else self.db_path.parent / "Backups"
        )
        backup_dir = root / timestamp
        backup_dir.mkdir(parents=True, exist_ok=False)

        database_backup = backup_dir / self.db_path.name
        source = self._connect()
        target = sqlite3.connect(database_backup)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        if self.trade_history_path.exists():
            trade_target = backup_dir / self.trade_history_path.name
            trade_target.write_bytes(self.trade_history_path.read_bytes())

        with self._connection() as conn:
            watchlist = [
                {"symbol": symbol, "name": name, "memo": memo, "created_at": created_at}
                for symbol, name, memo, created_at in conn.execute(
                    "select symbol, name, memo, created_at from watchlist order by id"
                )
            ]
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "database": database_backup.name,
            "trade_history": (
                self.trade_history_path.name if self.trade_history_path.exists() else ""
            ),
            "settings": self.all_app_settings(),
            "watchlist": watchlist,
        }
        (backup_dir / "backup_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return backup_dir

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
                    memo text not null default '',
                    created_at text not null
                )
                """
            )
            self._ensure_watchlist_columns(conn)
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
            conn.execute(
                """
                create table if not exists app_settings (
                    key text primary key,
                    value text not null,
                    updated_at text not null
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

    @staticmethod
    def _ensure_watchlist_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row[1])
            for row in conn.execute("pragma table_info(watchlist)")
        }
        if "memo" not in existing:
            conn.execute(
                "alter table watchlist add column memo text not null default ''"
            )

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
