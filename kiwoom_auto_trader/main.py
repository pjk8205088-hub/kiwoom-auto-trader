from __future__ import annotations

import sys


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
    _apply_windows_dpi_awareness()

    from kiwoom_auto_trader.ui import TraderApp

    app = TraderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
