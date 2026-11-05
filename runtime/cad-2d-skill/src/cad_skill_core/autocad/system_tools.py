"""Trusted Windows system-tool location independent of caller environment."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Callable


def trusted_system_directory(
    *, get_system_directory: Callable[[object, int], int] | None = None
) -> Path:
    """Return the WinAPI system directory without consulting process environment."""

    if get_system_directory is None:
        if os.name != "nt":
            raise OSError("trusted Windows system directory is unavailable off Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_system_directory = kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        get_system_directory.restype = ctypes.c_uint32
    size = 260
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = int(get_system_directory(buffer, size))
        if length == 0:
            raise OSError(
                ctypes.get_last_error(), "GetSystemDirectoryW failed"
            )
        if length < size:
            path = Path(buffer.value).resolve(strict=True)
            if not path.is_dir():
                raise OSError(f"WinAPI system directory is not accessible: {path}")
            return path
        size = length + 1
    raise OSError("GetSystemDirectoryW returned an invalid oversized path")
