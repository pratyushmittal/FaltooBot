import ctypes

from neonize._binder import Bytes


def _get_bytes(buffer: Bytes) -> bytes:
    # Read the raw pointer before Neonize's c_char_p field truncates it at a NUL byte.
    pointer = ctypes.c_void_p.from_address(ctypes.addressof(buffer) + Bytes.ptr.offset)
    return ctypes.string_at(pointer, buffer.size)


Bytes.get_bytes = _get_bytes  # type: ignore
