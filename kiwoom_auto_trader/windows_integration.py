from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path


STARTUP_VALUE_NAME = "KawaiiSecuritiesAutoTrader"


def startup_command() -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    pythonw = executable.with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else executable
    return f'"{launcher}" -m kiwoom_auto_trader.main'


def set_startup_registration(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        key_path,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                STARTUP_VALUE_NAME,
                0,
                winreg.REG_SZ,
                startup_command(),
            )
            return
        try:
            winreg.DeleteValue(key, STARTUP_VALUE_NAME)
        except FileNotFoundError:
            pass


class WindowsTrayIcon:
    RESTORE_COMMAND = 1023
    EXIT_COMMAND = 1024

    def __init__(self, tooltip: str, icon_path: str | Path | None = None) -> None:
        self.tooltip = str(tooltip or "카와이 증권")[:127]
        self.icon_path = Path(icon_path) if icon_path else None
        self.actions: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._hwnd = 0
        self._notify_id = None
        self._available = False

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._available
        self._started.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="kawaii-tray-icon",
            daemon=True,
        )
        self._thread.start()
        self._started.wait(timeout=3.0)
        return self._available

    def stop(self) -> None:
        if not self._hwnd:
            return
        try:
            import win32con
            import win32gui

            win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass

    def poll_action(self) -> str | None:
        try:
            return self.actions.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        try:
            import win32api
            import win32con
            import win32gui

            message_id = win32con.WM_USER + 20

            def on_destroy(hwnd, _message, _wparam, _lparam):
                if self._notify_id is not None:
                    win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self._notify_id)
                self._hwnd = 0
                win32gui.PostQuitMessage(0)
                return 0

            def on_command(_hwnd, _message, wparam, _lparam):
                command = win32api.LOWORD(wparam)
                if command == self.RESTORE_COMMAND:
                    self.actions.put("restore")
                elif command == self.EXIT_COMMAND:
                    self.actions.put("exit")
                return 0

            def on_notify(hwnd, _message, _wparam, lparam):
                if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
                    self.actions.put("restore")
                    return 0
                if lparam == win32con.WM_RBUTTONUP:
                    menu = win32gui.CreatePopupMenu()
                    win32gui.AppendMenu(
                        menu,
                        win32con.MF_STRING,
                        self.RESTORE_COMMAND,
                        "카와이 증권 열기",
                    )
                    win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
                    win32gui.AppendMenu(
                        menu,
                        win32con.MF_STRING,
                        self.EXIT_COMMAND,
                        "종료",
                    )
                    position = win32gui.GetCursorPos()
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.TrackPopupMenu(
                        menu,
                        win32con.TPM_LEFTALIGN,
                        position[0],
                        position[1],
                        0,
                        hwnd,
                        None,
                    )
                    win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
                    win32gui.DestroyMenu(menu)
                return 0

            window_class = win32gui.WNDCLASS()
            window_class.hInstance = win32api.GetModuleHandle(None)
            window_class.lpszClassName = f"KawaiiTraderTray{ id(self) }"
            window_class.lpfnWndProc = {
                win32con.WM_DESTROY: on_destroy,
                win32con.WM_COMMAND: on_command,
                message_id: on_notify,
            }
            class_atom = win32gui.RegisterClass(window_class)
            self._hwnd = win32gui.CreateWindow(
                class_atom,
                self.tooltip,
                0,
                0,
                0,
                win32con.CW_USEDEFAULT,
                win32con.CW_USEDEFAULT,
                0,
                0,
                window_class.hInstance,
                None,
            )
            if self.icon_path is not None and self.icon_path.exists():
                icon = win32gui.LoadImage(
                    0,
                    str(self.icon_path),
                    win32con.IMAGE_ICON,
                    0,
                    0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
                )
            else:
                icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            self._notify_id = (
                self._hwnd,
                0,
                win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                message_id,
                icon,
                self.tooltip,
            )
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._notify_id)
            self._available = True
            self._started.set()
            win32gui.PumpMessages()
        except Exception:
            self._available = False
            self._started.set()
