from __future__ import annotations

import argparse
import ctypes
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

from PIL import ImageGrab

from gba_make_minigame_state1_fix import mfm_entries

ROOT = Path(__file__).resolve().parents[1]

EFFECT_GROUPS = {
    "가짜 호객": [0x40, 0x41, 0x42, 0x43],
    "가짜 회원": [0x44, 0x45, 0x46, 0x47],
    "가짜 판매": [0x48, 0x49, 0x4A, 0x4B],
    "가드": [0x4C, 0x4D, 0x4E, 0x4F],
    "가짜 호객(중복)": [0x54, 0x55, 0x56, 0x57],
    "가짜 회원(중복)": [0x58, 0x59, 0x5A, 0x5B],
    "가짜 판매(중복)": [0x5C, 0x5D, 0x5E, 0x5F],
}

BASE_GROUPS = {
    "호객": [0x30, 0x31, 0x32],
    "판매": [0x34, 0x35, 0x36],
    "정돈": [0x38, 0x39, 0x3A],
    "회원": [0x3C, 0x3D, 0x3E],
}

MFM_ENTRY0 = 0xD8178C
MFM_ENTRY_SIZE = 0x42
EFFECT_START = MFM_ENTRY0 + (0x40 - 0x30) * MFM_ENTRY_SIZE
EFFECT_END = MFM_ENTRY0 + (0x60 - 0x30) * MFM_ENTRY_SIZE


def _find_window_for_pid(pid: int, timeout: float = 10.0) -> int:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    result: list[tuple[int, int, str, tuple[int, int]]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result.clear()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_proc(hwnd: int, _lparam: int) -> bool:
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid and user32.IsWindowVisible(hwnd):
                rect = wintypes.RECT()
                if user32.GetClientRect(hwnd, ctypes.byref(rect)):
                    width = max(0, rect.right - rect.left)
                    height = max(0, rect.bottom - rect.top)
                    title_len = user32.GetWindowTextLengthW(hwnd)
                    title_buf = ctypes.create_unicode_buffer(title_len + 1)
                    user32.GetWindowTextW(hwnd, title_buf, title_len + 1)
                    result.append((width * height, int(hwnd), title_buf.value, (width, height)))
            return True

        user32.EnumWindows(enum_proc, 0)
        if result:
            result.sort(reverse=True)
            print(
                "windows="
                + repr([(hex(hwnd), title, size) for _, hwnd, title, size in result])
            )
            # Prefer the largest visible mGBA window.  Modal warning windows are
            # normally smaller and should never be mistaken for the game view.
            return result[0][1]
        time.sleep(0.05)
    raise RuntimeError(f"mGBA window not found for pid {pid}")


def _capture_client(hwnd: int, path: Path) -> tuple[int, int]:
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32 = ctypes.windll.user32
    # ImageGrab captures desktop pixels, not an obscured OpenGL client surface.
    # Put mGBA in front immediately before sampling its client rectangle.
    SW_RESTORE = 9
    HWND_TOPMOST = ctypes.c_void_p(-1)
    HWND_NOTOPMOST = ctypes.c_void_p(-2)
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    top_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, top_flags)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.35)

    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    origin = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError()
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    image = ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + width, origin.y + height), all_screens=True)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return image.size


def _rewrite_state_rom_crc(state: bytes, rom_crc32: int) -> tuple[bytes, int]:
    """Rewrite only GBASerializedState.romCrc32 inside an mGBA PNG savestate."""
    if state[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("unexpected mGBA savestate container")
    out = bytearray(state[:8])
    pos = 8
    seen = 0
    old_crc = -1
    while pos + 12 <= len(state):
        length = struct.unpack_from(">I", state, pos)[0]
        chunk_type = state[pos + 4:pos + 8]
        data = state[pos + 8:pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"gbAs":
            raw = bytearray(zlib.decompress(data))
            if len(raw) != 0x61000:
                raise RuntimeError(f"unexpected GBA state size: {len(raw)}")
            old_crc = struct.unpack_from("<I", raw, 8)[0]
            struct.pack_into("<I", raw, 8, rom_crc32)
            data = zlib.compress(bytes(raw), 9)
            seen += 1
        out += struct.pack(">I", len(data))
        out += chunk_type
        out += data
        out += struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        if chunk_type == b"IEND":
            break
    if seen != 1 or old_crc < 0:
        raise RuntimeError(f"expected exactly one gbAs chunk, found {seen}")
    return bytes(out), old_crc


def _runtime_pattern(entries: dict[int, bytes], codes: list[int]) -> bytes:
    # 32x16 OBJ is stored as four 8x16 cells in 2D OBJ VRAM: the four top 8x8
    # tiles are consecutive, followed by the four bottom 8x8 tiles.
    if len(codes) != 4:
        raise ValueError(codes)
    return b"".join(entries[code][:32] for code in codes) + b"".join(
        entries[code][32:] for code in codes
    )


def _readable_writable_regions(pid: int):
    from ctypes import wintypes

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    MEM_COMMIT = 0x1000
    PAGE_GUARD = 0x100
    PAGE_NOACCESS = 0x01
    WRITABLE = {0x04, 0x08, 0x40, 0x80}

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise ctypes.WinError()
    try:
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        max_address = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 1)) - 1
        while address < max_address:
            n = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not n:
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            protect = int(mbi.Protect)
            base_protect = protect & 0xFF
            if (
                mbi.State == MEM_COMMIT
                and size > 0
                and not (protect & PAGE_GUARD)
                and base_protect != PAGE_NOACCESS
                and base_protect in WRITABLE
            ):
                yield handle, kernel32, base, size, protect
            next_address = base + size
            if next_address <= address:
                break
            address = next_address
    finally:
        kernel32.CloseHandle(handle)


def _patch_host_rom_buffer(
    pid: int,
    old_span: bytes,
    new_span: bytes,
    rom_prefix: bytes,
    span_offset: int,
) -> list[int]:
    """Replace the effect-MFM span in mGBA's writable host GamePak buffer.

    This lets a savestate remain paired with the exact ROM it was created from
    while testing a graphics-only ROM change.  The state is loaded normally;
    only the cartridge bytes backing 0x40..0x5F are replaced before the idle
    verification interval begins.
    """
    from ctypes import wintypes

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_OPERATION = 0x0008
    PROCESS_VM_READ = 0x0010
    PROCESS_VM_WRITE = 0x0020
    MEM_COMMIT = 0x1000
    PAGE_GUARD = 0x100
    PAGE_NOACCESS = 0x01
    WRITABLE = {0x04, 0x08, 0x40, 0x80}

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.WriteProcessMemory.restype = wintypes.BOOL

    access = PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise ctypes.WinError()

    prefix = old_span[:256]
    patched: list[int] = []
    try:
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        max_address = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 1)) - 1
        while address < max_address:
            n = kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if not n:
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            protect = int(mbi.Protect)
            base_protect = protect & 0xFF
            if (
                mbi.State == MEM_COMMIT
                and size >= len(old_span)
                and not (protect & PAGE_GUARD)
                and base_protect != PAGE_NOACCESS
                and base_protect in WRITABLE
            ):
                offset = 0
                overlap = b""
                while offset < size:
                    chunk_size = min(4 * 1024 * 1024, size - offset)
                    buf = ctypes.create_string_buffer(chunk_size)
                    got = ctypes.c_size_t()
                    if not kernel32.ReadProcessMemory(
                        handle, ctypes.c_void_p(base + offset), buf, chunk_size, ctypes.byref(got)
                    ):
                        break
                    block = overlap + bytes(buf.raw[:got.value])
                    block_base = base + offset - len(overlap)
                    at = block.find(prefix)
                    while at >= 0:
                        hit = block_base + at
                        if base <= hit and hit + len(old_span) <= base + size:
                            verify = ctypes.create_string_buffer(len(old_span))
                            verify_got = ctypes.c_size_t()
                            if kernel32.ReadProcessMemory(
                                handle, ctypes.c_void_p(hit), verify, len(old_span), ctypes.byref(verify_got)
                            ) and bytes(verify.raw[:verify_got.value]) == old_span:
                                # A savestate/decompression scratch buffer can
                                # contain the same MFM bytes.  Only accept a hit
                                # whose inferred ROM base also contains the real
                                # GBA header/prefix.
                                rom_base = hit - span_offset
                                header_ok = False
                                if base <= rom_base and rom_base + len(rom_prefix) <= base + size:
                                    header = ctypes.create_string_buffer(len(rom_prefix))
                                    header_got = ctypes.c_size_t()
                                    if kernel32.ReadProcessMemory(
                                        handle,
                                        ctypes.c_void_p(rom_base),
                                        header,
                                        len(rom_prefix),
                                        ctypes.byref(header_got),
                                    ):
                                        header_ok = bytes(header.raw[:header_got.value]) == rom_prefix
                                if header_ok:
                                    src = ctypes.create_string_buffer(new_span)
                                    written = ctypes.c_size_t()
                                    if not kernel32.WriteProcessMemory(
                                        handle, ctypes.c_void_p(hit), src, len(new_span), ctypes.byref(written)
                                    ):
                                        raise ctypes.WinError()
                                    if written.value != len(new_span):
                                        raise RuntimeError(
                                            f"short host ROM write at 0x{hit:X}: {written.value}"
                                        )
                                    patched.append(hit)
                        at = block.find(prefix, at + 1)
                    if got.value == 0:
                        break
                    overlap = block[-(len(prefix) - 1):]
                    offset += got.value
            next_address = base + size
            if next_address <= address:
                break
            address = next_address
    finally:
        kernel32.CloseHandle(handle)

    if not patched:
        raise RuntimeError("mGBA writable GamePak buffer not found")
    return patched


def _scan_process(pid: int, patterns: dict[str, bytes]) -> dict[str, list[dict[str, object]]]:
    hits: dict[str, list[dict[str, object]]] = {label: [] for label in patterns}
    max_pattern = max(len(p) for p in patterns.values())
    for handle, kernel32, base, size, protect in _readable_writable_regions(pid):
        offset = 0
        overlap = b""
        while offset < size:
            n = min(4 * 1024 * 1024, size - offset)
            buf = ctypes.create_string_buffer(n)
            got = ctypes.c_size_t()
            if not kernel32.ReadProcessMemory(
                handle,
                ctypes.c_void_p(base + offset),
                buf,
                n,
                ctypes.byref(got),
            ):
                break
            block = overlap + bytes(buf.raw[: got.value])
            block_base = base + offset - len(overlap)
            for label, pattern in patterns.items():
                at = block.find(pattern)
                while at >= 0:
                    address = block_base + at
                    if not hits[label] or hits[label][-1]["address"] != hex(address):
                        hits[label].append(
                            {
                                "address": hex(address),
                                "region": [hex(base), hex(base + size)],
                                "protect": hex(protect),
                            }
                        )
                    at = block.find(pattern, at + 1)
            if got.value == 0:
                break
            overlap = block[-(max_pattern - 1) :] if max_pattern > 1 else b""
            offset += got.value
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mgba", type=Path, required=True)
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--patched-rom", type=Path)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--state-load-delay", type=float, default=0.75)
    ap.add_argument("--direct-patched-state", action="store_true")
    ap.add_argument("--rewritten-state-out", type=Path)
    ap.add_argument("--screenshot", type=Path)
    ap.add_argument("--require-label", default="가짜 호객")
    args = ap.parse_args()

    mgba = args.mgba.resolve()
    rom_path = args.rom.resolve()
    state_path = args.state.resolve()
    base_bytes = rom_path.read_bytes()
    patched_bytes = base_bytes
    changed: list[int] = []
    if args.patched_rom is not None:
        patched_path = args.patched_rom.resolve()
        patched_bytes = patched_path.read_bytes()
        if len(patched_bytes) != len(base_bytes):
            raise RuntimeError("patched ROM size mismatch")
        changed = [i for i, (old, new) in enumerate(zip(base_bytes, patched_bytes)) if old != new]
        outside = [i for i in changed if not (EFFECT_START <= i < EFFECT_END)]
        if outside:
            raise RuntimeError(
                "patched ROM differs outside effect MFM range: "
                + ", ".join(hex(i) for i in outside[:16])
            )
    entries = mfm_entries(patched_bytes)
    patterns = {label: _runtime_pattern(entries, codes) for label, codes in EFFECT_GROUPS.items()}

    with tempfile.TemporaryDirectory(prefix="digicomm_mgba_verify_") as tmp:
        tmp_dir = Path(tmp)
        temp_rom = tmp_dir / "runtime_verify.gba"
        input_state = tmp_dir / "input.ss1"
        if args.direct_patched_state:
            if args.patched_rom is None:
                raise RuntimeError("--direct-patched-state requires --patched-rom")
            shutil.copy2(args.patched_rom.resolve(), temp_rom)
            new_crc = zlib.crc32(patched_bytes) & 0xFFFFFFFF
            rewritten_state, old_crc = _rewrite_state_rom_crc(state_path.read_bytes(), new_crc)
            expected_old_crc = zlib.crc32(base_bytes) & 0xFFFFFFFF
            if old_crc != expected_old_crc:
                raise RuntimeError(
                    f"state/base CRC mismatch before rewrite: 0x{old_crc:08X} != 0x{expected_old_crc:08X}"
                )
            input_state.write_bytes(rewritten_state)
            if args.rewritten_state_out is not None:
                args.rewritten_state_out.resolve().write_bytes(rewritten_state)
            print(
                f"state_crc_rewrite=0x{old_crc:08X}->0x{new_crc:08X} "
                f"state_body=unchanged_except_rom_crc32"
            )
        else:
            shutil.copy2(rom_path, temp_rom)
            shutil.copy2(state_path, input_state)

        proc = subprocess.Popen(
            [str(mgba), "-t", str(input_state), str(temp_rom)],
            cwd=str(tmp_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            hwnd = _find_window_for_pid(proc.pid)
            host_hits: list[int] = []
            if args.patched_rom is not None and not args.direct_patched_state:
                # The Qt frontend creates its window slightly before -t state
                # restoration/checksum validation has finished.  Patching the
                # GamePak buffer at window creation can therefore make mGBA
                # reject an otherwise matching savestate.  Let state loading
                # finish first; no controller input is sent during this delay.
                time.sleep(args.state_load_delay)
                host_hits = _patch_host_rom_buffer(
                    proc.pid,
                    base_bytes[EFFECT_START:EFFECT_END],
                    patched_bytes[EFFECT_START:EFFECT_END],
                    base_bytes[:512],
                    EFFECT_START,
                )
                print(
                    f"state_load_delay={args.state_load_delay:.3f} "
                    f"hotpatch=0x{EFFECT_START:X}..0x{EFFECT_END:X} "
                    f"changed_bytes={len(changed)} host_hits={[hex(x) for x in host_hits]}"
                )
            # No controller input is sent at any point; just let state1 advance.
            # The requested five-second interval starts only after any graphics
            # hotpatch is complete.
            time.sleep(args.seconds)
            screenshot_size = None
            if args.screenshot is not None:
                screenshot_size = _capture_client(hwnd, args.screenshot.resolve())
            hits = _scan_process(proc.pid, patterns)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)

    print(f"seconds={args.seconds:.3f}")
    if screenshot_size is not None:
        print(f"screenshot={args.screenshot.resolve()} size={screenshot_size}")
    for label, label_hits in hits.items():
        if label_hits:
            print(f"{label}: {label_hits}")
    if not hits.get(args.require_label):
        raise RuntimeError(f"required runtime label pattern not found: {args.require_label}")


if __name__ == "__main__":
    main()
