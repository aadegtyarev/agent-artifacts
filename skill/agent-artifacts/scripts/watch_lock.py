"""Advisory lock on POSIX, native shared/exclusive byte-range lock on Windows."""

import os

if os.name == "nt":
    import ctypes
    import msvcrt
    import time
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _lock = _kernel32.LockFileEx
    _lock.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _lock.restype = wintypes.BOOL
    _unlock = _kernel32.UnlockFileEx
    _unlock.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _unlock.restype = wintypes.BOOL

    def acquire(fd, *, shared=False, wait=False):
        # Windows locks also deny access to the bytes to other handles. Place
        # the lock beyond EOF, away from the small Pi binding record.
        at = _Overlapped(Offset=8192)
        flags = 1 | (0 if shared else 2)  # FAIL_IMMEDIATELY, EXCLUSIVE_LOCK
        while not _lock(msvcrt.get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(at)):
            error = ctypes.get_last_error()
            if error != 33:  # ERROR_LOCK_VIOLATION
                raise ctypes.WinError(error)
            if not wait:
                return False
            time.sleep(0.01)
        return True

    def release(fd):
        at = _Overlapped(Offset=8192)
        if not _unlock(msvcrt.get_osfhandle(fd), 0, 1, 0, ctypes.byref(at)):
            raise ctypes.WinError(ctypes.get_last_error())

else:
    import fcntl

    def acquire(fd, *, shared=False, wait=False):
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(fd, mode if wait else mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def release(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
