import unittest

from kiwoom_auto_trader.main import (
    ERROR_ALREADY_EXISTS,
    _acquire_single_instance_mutex,
    _release_single_instance_mutex,
)


class FakeKernel32:
    def __init__(self, last_error: int = 0) -> None:
        self.last_error = last_error
        self.handle = 101
        self.closed_handles: list[int] = []

    def CreateMutexW(self, _attributes, _owner, _name):
        return self.handle

    def GetLastError(self):
        return self.last_error

    def CloseHandle(self, handle):
        self.closed_handles.append(handle)
        return True


class MainTests(unittest.TestCase):
    def test_acquires_and_releases_single_instance_mutex(self):
        kernel32 = FakeKernel32()

        handle = _acquire_single_instance_mutex(kernel32)
        _release_single_instance_mutex(handle, kernel32)

        self.assertEqual(handle, kernel32.handle)
        self.assertEqual(kernel32.closed_handles, [kernel32.handle])

    def test_rejects_second_instance_and_closes_duplicate_handle(self):
        kernel32 = FakeKernel32(last_error=ERROR_ALREADY_EXISTS)

        handle = _acquire_single_instance_mutex(kernel32)

        self.assertIsNone(handle)
        self.assertEqual(kernel32.closed_handles, [kernel32.handle])


if __name__ == "__main__":
    unittest.main()
