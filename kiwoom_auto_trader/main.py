from __future__ import annotations

import sys
from typing import Any


INSTANCE_MUTEX_NAME = "Local\\KiwoomAutoTrader.SingleInstance"
ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_mutex(kernel32: Any | None = None) -> Any | None:
    if sys.platform != "win32":
        return -1

    import ctypes
    from ctypes import wintypes

    library = kernel32 or ctypes.windll.kernel32
    create_mutex = library.CreateMutexW
    if kernel32 is None:
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE

    handle = create_mutex(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        raise OSError("자동매매 프로그램 단일 실행 잠금을 만들지 못했습니다.")
    if int(library.GetLastError()) == ERROR_ALREADY_EXISTS:
        library.CloseHandle(handle)
        return None
    return handle


def _release_single_instance_mutex(handle: Any, kernel32: Any | None = None) -> None:
    if handle in (None, -1) or sys.platform != "win32":
        return

    import ctypes

    library = kernel32 or ctypes.windll.kernel32
    library.CloseHandle(handle)


def _show_already_running_message() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "키움 자동매매가 이미 실행 중입니다. 기존 창을 사용해 주세요.",
            "키움 자동매매",
            0x30,
        )
    except Exception:
        pass


def _apply_windows_dpi_awareness() -> bool:
    """Set DPI awareness before tkinter creates any native windows."""
    if sys.platform != "win32":
        return False

    try:
        import ctypes

        try:
            set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = ctypes.c_bool
            if set_context(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                return True
        except Exception:
            pass

        try:
            result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
            if result == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
                return True
        except Exception:
            pass

        try:
            return bool(ctypes.windll.user32.SetProcessDPIAware())
        except Exception:
            return False
    except Exception:
        return False


def main() -> None:
    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        _show_already_running_message()
        return

    try:
        _apply_windows_dpi_awareness()

        from kiwoom_auto_trader.ui import TraderApp

        app = TraderApp()
        app.mainloop()
    finally:
        _release_single_instance_mutex(mutex_handle)


if __name__ == "__main__":
    main()
