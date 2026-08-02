from __future__ import annotations

import sys
from queue import Queue
from threading import Thread

try:
    import pythoncom
    import win32com.client
except ImportError:  # pragma: no cover - available in the Windows EXE runtime
    pythoncom = None
    win32com = None


class OrderVoiceNotifier:
    """Speak actual buy/sell executions through the Windows SAPI voice."""

    PHRASES = {
        "BUY": "매수되었습니다",
        "SELL": "매도되었습니다",
    }

    def __init__(self) -> None:
        self.last_error = ""
        self._messages: Queue[str | None] = Queue()
        self._thread: Thread | None = None
        self._available = bool(
            sys.platform == "win32" and pythoncom is not None and win32com is not None
        )
        if self._available:
            self._thread = Thread(
                target=self._run,
                name="KawaiiSecuritiesTTS",
                daemon=True,
            )
            self._thread.start()

    @property
    def available(self) -> bool:
        return self._available

    def announce_execution(self, side: str) -> bool:
        phrase = self.PHRASES.get(str(side or "").strip().upper())
        if not phrase or not self._available:
            return False
        self._messages.put(phrase)
        return True

    def close(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._messages.put(None)

    def _run(self) -> None:
        assert pythoncom is not None
        assert win32com is not None
        pythoncom.CoInitialize()
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            while True:
                message = self._messages.get()
                if message is None:
                    return
                try:
                    voice.Speak(message)
                    self.last_error = ""
                except Exception as exc:  # COM errors vary by installed voice engine
                    self.last_error = str(exc)
        except Exception as exc:  # COM errors vary by Windows installation
            self.last_error = str(exc)
            self._available = False
        finally:
            pythoncom.CoUninitialize()
