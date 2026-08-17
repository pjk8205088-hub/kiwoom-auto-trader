"""Small, safe handoff helpers for the KB desktop HTS.

KB HTS is a separate desktop application.  This module intentionally does
not automate clicks or submit orders: it only detects a running HTS window,
can launch a user-selected executable, and prepares values for a manual
handoff.  A broker-supported KB API can be added behind this boundary later.
"""

from __future__ import annotations

import csv
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .symbols import normalize_symbol


KB_HTS_TITLE_HINTS = ("h-able", "hable", "kb증권", "kb securities")
KB_HTS_PROCESS_HINTS = ("h-able", "hable", "kbsec", "kb증권")


@dataclass(frozen=True)
class KbHtsStatus:
    """The best available local status of the KB HTS desktop application."""

    process_found: bool = False
    window_found: bool = False
    window_title: str = ""
    checked_at: datetime | None = None
    message: str = "KB HTS OFF · 실행 상태를 확인해 주세요."

    @property
    def connected(self) -> bool:
        return self.process_found or self.window_found


def _tasklist_process_names() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    try:
        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    names: list[str] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if row:
            names.append(row[0].strip())
    return tuple(names)


def _visible_window_titles() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    try:
        import win32gui  # type: ignore[import-not-found]
    except ImportError:
        return ()

    titles: list[str] = []

    def collect(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = str(win32gui.GetWindowText(hwnd) or "").strip()
        if title:
            titles.append(title)

    try:
        win32gui.EnumWindows(collect, None)
    except (OSError, RuntimeError):
        return ()
    return tuple(titles)


def _contains_hint(value: str, hints: tuple[str, ...]) -> bool:
    text = str(value or "").casefold()
    return any(hint.casefold() in text for hint in hints)


def detect_kb_hts() -> KbHtsStatus:
    """Detect the separately running KB HTS process/window on Windows."""

    process_found = any(
        _contains_hint(name, KB_HTS_PROCESS_HINTS)
        for name in _tasklist_process_names()
    )
    window_title = next(
        (
            title
            for title in _visible_window_titles()
            if _contains_hint(title, KB_HTS_TITLE_HINTS)
        ),
        "",
    )
    window_found = bool(window_title)
    if window_found:
        message = f"KB HTS ON · {window_title}"
    elif process_found:
        message = "KB HTS ON · 프로세스 실행 중"
    else:
        message = "KB HTS OFF · 프로그램을 실행해 주세요."
    return KbHtsStatus(
        process_found=process_found,
        window_found=window_found,
        window_title=window_title,
        checked_at=datetime.now(),
        message=message,
    )


def launch_kb_hts(executable_path: str = "") -> tuple[bool, str]:
    """Launch a user-selected KB HTS executable without guessing its path."""

    path_text = str(executable_path or "").strip().strip('"')
    if not path_text:
        return False, "KB HTS 실행 파일 경로를 설정해 주세요."
    path = Path(path_text).expanduser()
    if not path.is_file():
        return False, f"KB HTS 실행 파일을 찾을 수 없습니다: {path}"
    try:
        subprocess.Popen([str(path)], cwd=str(path.parent))
    except OSError as exc:
        return False, f"KB HTS 실행 실패: {exc}"
    return True, f"KB HTS 실행 요청 완료: {path.name}"


def focus_kb_hts_window() -> bool:
    """Bring a matching KB HTS window forward when pywin32 is available."""

    if os.name != "nt":
        return False
    try:
        import win32gui  # type: ignore[import-not-found]
    except ImportError:
        return False
    matched: list[int] = []

    def collect(hwnd: int, _extra: object) -> None:
        if win32gui.IsWindowVisible(hwnd) and _contains_hint(
            win32gui.GetWindowText(hwnd), KB_HTS_TITLE_HINTS
        ):
            matched.append(hwnd)

    try:
        win32gui.EnumWindows(collect, None)
        if not matched:
            return False
        win32gui.ShowWindow(matched[0], 5)
        win32gui.SetForegroundWindow(matched[0])
        return True
    except (OSError, RuntimeError):
        return False


def normalize_kb_symbol(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) != 6 or not raw.isdigit():
        return ""
    symbol = normalize_symbol(raw)
    if len(symbol) != 6 or not symbol.isdigit() or symbol == "000000":
        return ""
    return symbol


def build_kb_handoff_text(
    symbol: object,
    name: object,
    price: object,
    side: object,
    quantity: object,
) -> str:
    """Create a readable clipboard payload for manual KB HTS entry."""

    normalized = normalize_kb_symbol(symbol) or "미설정"
    display_name = str(name or "").strip() or "종목명 미조회"
    try:
        price_value = float(price or 0)
    except (TypeError, ValueError):
        price_value = 0.0
    try:
        quantity_value = max(0, int(quantity or 0))
    except (TypeError, ValueError):
        quantity_value = 0
    side_label = "매수" if str(side or "").upper() == "BUY" else "매도"
    price_label = f"{price_value:,.0f}원" if price_value > 0 else "현재가 미조회"
    return (
        "KB HTS 수동 주문 입력값\n"
        f"종목코드\t{normalized}\n"
        f"종목명\t{display_name}\n"
        f"현재가\t{price_label}\n"
        f"구분\t{side_label}\n"
        f"수량\t{quantity_value}주"
    )
