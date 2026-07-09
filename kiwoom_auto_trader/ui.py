from __future__ import annotations

import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import cast

from .models import PatternState, StrategySettings
from .service import AutoTradingService
from .storage import Storage


class TraderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Kiwoom Auto Trader")
        self.geometry("1040x720")
        self.minsize(920, 620)

        db_path = Path(tempfile.gettempdir()) / "kiwoom_auto_trader.sqlite3"
        self.service = AutoTradingService(storage=Storage(db_path))
        self._refresh_after_id: str | None = None
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Kiwoom Auto Trader - Mock Mode", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(header, text="Emergency Stop", command=self._emergency_stop).grid(row=0, column=1)

        controls = ttk.LabelFrame(self, text="Settings", padding=12)
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        for idx in range(8):
            controls.columnconfigure(idx, weight=1)

        self.symbol_var = tk.StringVar(value="005930")
        self.capital_var = tk.StringVar(value="1000000")
        self.price_var = tk.StringVar(value="72000")
        self.period_var = tk.StringVar(value="20")
        self.upper_var = tk.StringVar(value="100")
        self.pattern_var = tk.StringVar(value="NONE")
        self.use_cci_var = tk.BooleanVar(value=True)

        self._field(controls, "Symbol", self.symbol_var, 0)
        self._field(controls, "Max Capital", self.capital_var, 1)
        self._field(controls, "Mock Price", self.price_var, 2)
        self._field(controls, "CCI Period", self.period_var, 3)
        self._field(controls, "CCI Upper", self.upper_var, 4)

        ttk.Label(controls, text="Pattern").grid(row=0, column=5, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.pattern_var,
            values=("NONE", "BULLISH", "BEARISH"),
            state="readonly",
            width=12,
        ).grid(row=1, column=5, sticky="ew", padx=(0, 8))

        ttk.Checkbutton(controls, text="Use CCI Filter", variable=self.use_cci_var).grid(
            row=1, column=6, sticky="w"
        )
        ttk.Button(controls, text="Save", command=self._save_settings).grid(row=1, column=7, sticky="ew")

        actions = ttk.Frame(controls)
        actions.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="Start", command=self._start).pack(side="left")
        ttk.Button(actions, text="Stop", command=self._stop).pack(side="left", padx=6)
        ttk.Button(actions, text="Run One Tick", command=self._run_tick).pack(side="left")
        ttk.Button(actions, text="Mock Next Sell Failure", command=self._fail_sell).pack(side="left", padx=6)

        body = ttk.Frame(self, padding=12)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        self.status_text = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.status_text, font=("Segoe UI", 11)).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12)
        )

        self.orders = self._table(
            body,
            "Recent Orders",
            ("time", "symbol", "side", "qty", "price", "ok", "message"),
            0,
        )
        self.logs = self._table(body, "Recent Logs", ("time", "level", "category", "message"), 1)

    def _field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=1, column=column, sticky="ew", padx=(0, 8)
        )

    def _table(self, parent: ttk.Frame, title: str, columns: tuple[str, ...], column: int) -> ttk.Treeview:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=1, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        table = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        for name in columns:
            table.heading(name, text=name)
            table.column(name, width=90, stretch=True)
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scrollbar.set)
        return table

    def _save_settings(self) -> None:
        settings = StrategySettings(
            cci_period=max(1, int(float(self.period_var.get()))),
            cci_upper=float(self.upper_var.get()),
            cci_lower=-abs(float(self.upper_var.get())),
            use_cci_filter=self.use_cci_var.get(),
        )
        self.service.configure(self.symbol_var.get(), float(self.capital_var.get()), settings)
        self._refresh()

    def _start(self) -> None:
        self._save_settings()
        self.service.start()
        self._refresh()

    def _stop(self) -> None:
        self.service.stop()
        self._refresh()

    def _emergency_stop(self) -> None:
        self.service.emergency_stop()
        self._refresh()

    def _fail_sell(self) -> None:
        broker = self.service.broker
        if hasattr(broker, "fail_next_sell"):
            broker.fail_next_sell = True
        self.service.storage.log("WARN", "MOCK", "Next sell will fail once.")
        self._refresh()

    def _run_tick(self) -> None:
        pattern = cast(PatternState, self.pattern_var.get())
        self.service.step(pattern, float(self.price_var.get()))
        self._refresh()

    def _refresh(self) -> None:
        snapshot = self.service.snapshot()
        decision = snapshot.decision.action if snapshot.decision else "NONE"
        cci = ""
        if snapshot.decision and snapshot.decision.cci_value is not None:
            cci = f" | CCI {snapshot.decision.cci_value:.2f}"
        self.status_text.set(
            " | ".join(
                [
                    f"Connection {snapshot.connection}",
                    f"Running {snapshot.running}",
                    f"Symbol {snapshot.symbol}",
                    f"Pattern {snapshot.pattern}",
                    f"Price {snapshot.price:,.0f}",
                    f"Position {snapshot.quantity}",
                    f"Avg {snapshot.average_price:,.0f}",
                    f"Decision {decision}{cci}",
                ]
            )
        )
        self._replace_rows(self.orders, snapshot.orders)
        self._replace_rows(self.logs, snapshot.logs)
        self._schedule_auto_tick()

    def _auto_tick(self) -> None:
        self._refresh_after_id = None
        if self.service.running:
            self._run_tick()
        else:
            self._refresh()

    def _schedule_auto_tick(self) -> None:
        if self._refresh_after_id is None:
            self._refresh_after_id = self.after(3000, self._auto_tick)

    def _replace_rows(self, table: ttk.Treeview, rows: list[tuple]) -> None:
        for item in table.get_children():
            table.delete(item)
        for row in rows:
            table.insert("", "end", values=row)
