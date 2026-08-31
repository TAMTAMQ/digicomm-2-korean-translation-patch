"""digicomm_nyo MBM/MCM graphics container.

MBM  +0  'MBM\0'  +6 u16 width  +8 u16 height  +10 u16 palette entries
     +14 u16 tile count  +16 u32 palette ptr  +20 u32 next ptr  +24 MCM
MCM  +0  'MCM\0'  +4 u32 decompressed size  +8 u32 0x1000
     +12 u32 section count  +16 u8 codec(3)  +20.. u32 section pointers
Sections are BIOS Huffman streams (header byte 0x24 = Huffman, 4-bit symbols).
"""
import struct


def huff_decode(data, pos=0):
    hdr = struct.unpack_from('<I', data, pos)[0]
    kind, bits, out_size = (hdr >> 4) & 0xF, hdr & 0xF, hdr >> 8
    if kind != 2 or bits not in (4, 8):
        raise ValueError('not a BIOS huffman stream: 0x%08X' % hdr)
    tree = pos + 4
    tree_end = tree + (data[tree] + 1) * 2
    node, out, half = tree + 1, bytearray(), None
    p = tree_end
    while len(out) < out_size:
        word = struct.unpack_from('<I', data, p)[0]
        p += 4
        for k in range(31, -1, -1):
            bit = (word >> k) & 1
            cur = data[node]
            nxt = (node & ~1) + (cur & 0x3F) * 2 + 2 + bit
            leaf = cur & (0x40 if bit else 0x80)
            node = nxt
            if leaf:
                val = data[node]
                if bits == 4:
                    half = val if half is None else out.append(half | (val << 4)) or None
                else:
                    out.append(val)
                node = tree + 1
                if len(out) >= out_size:
                    break
    return bytes(out[:out_size])


def lz77_decode(data, pos=0):
    hdr = struct.unpack_from('<I', data, pos)[0]
    out_size, i, out = hdr >> 8, pos + 4, bytearray()
    while len(out) < out_size:
        flags = data[i]; i += 1
        for b in range(8):
            if len(out) >= out_size:
                break
            if flags & (0x80 >> b):
                hi, lo = data[i], data[i + 1]; i += 2
                n, disp = (hi >> 4) + 3, ((hi & 0xF) << 8 | lo) + 1
                for _ in range(n):
                    out.append(out[-disp])
            else:
                out.append(data[i]); i += 1
    return bytes(out[:out_size])


def rle_decode(data, pos=0):
    hdr = struct.unpack_from('<I', data, pos)[0]
    out_size, i, out = hdr >> 8, pos + 4, bytearray()
    while len(out) < out_size:
        f = data[i]; i += 1
        if f & 0x80:
            out += bytes([data[i]]) * ((f & 0x7F) + 3); i += 1
        else:
            n = (f & 0x7F) + 1
            out += data[i:i + n]; i += n
    return bytes(out[:out_size])


def decompress(data, pos=0):
    """Dispatch on the BIOS compression header byte."""
    kind = data[pos] & 0xF0
    if kind == 0x20:
        return huff_decode(data, pos)
    if kind == 0x10:
        return lz77_decode(data, pos)
    if kind == 0x30:
        return rle_decode(data, pos)
    raise ValueError('unknown codec 0x%02X at 0x%X' % (data[pos], pos))


def read_mcm(rom, off):
    """Sections of one MCM block, decompressed.

    +16 selects the section format, and it is authoritative -- the reader does
    NOT dispatch on the stream's own header byte:

        0  raw, merely chunked by +8 (0x1000) with the last section short
        1  BIOS RLE      (streams begin 0x30)
        2  BIOS LZ77     (streams begin 0x10)
        3  BIOS Huffman  (streams begin 0x20)

    Across the ROM this holds exactly: 125 codec-1 blocks against 125 0x30
    sections, 225 codec-2 against 225 0x10, 1426 codec-3 against 3305 0x20.
    Writing a stream whose header disagrees with the field makes the game run
    the wrong decompressor and execute the result.
    """
    _EXPECT = {1: 0x30, 2: 0x10, 3: 0x20}
    assert rom[off:off + 4] == b'MCM' + bytes(1), 'no MCM at 0x%X' % off
    size, chunk, nsec = struct.unpack_from('<III', rom, off + 4)
    codec = rom[off + 16]
    ptrs = [struct.unpack_from('<I', rom, off + 20 + i * 4)[0] - 0x08000000
            for i in range(nsec)]
    out = []
    left = size
    for p in ptrs:
        if codec == 0:
            n = min(chunk, left)
            out.append(bytes(rom[p:p + n]))
        else:
            want = _EXPECT.get(codec)
            if want is None:
                raise ValueError('unknown MCM codec %d at 0x%X' % (codec, off))
            if rom[p] & 0xF0 != want:
                raise ValueError('MCM 0x%X codec %d wants a 0x%02X stream, '
                                 'found 0x%02X at 0x%X'
                                 % (off, codec, want, rom[p], p))
            out.append(decompress(rom, p))
        left -= len(out[-1])
    return out


def read_mbm(rom, off):
    assert rom[off:off + 4] == b'MBM\x00', 'no MBM at 0x%X' % off
    w, h, pal_n = struct.unpack_from('<HHH', rom, off + 6)
    tiles = struct.unpack_from('<H', rom, off + 14)[0]
    pal_ptr = struct.unpack_from('<I', rom, off + 16)[0] - 0x08000000
    pal = [struct.unpack_from('<H', rom, pal_ptr + i * 2)[0] for i in range(pal_n)]
    m = off + 24
    assert rom[m:m + 4] == b'MCM\x00', 'no MCM at 0x%X' % m
    size, _, nsec = struct.unpack_from('<III', rom, m + 12 - 8)
    ptrs = [struct.unpack_from('<I', rom, m + 20 + i * 4)[0] - 0x08000000
            for i in range(nsec)]
    secs = [decompress(rom, p) for p in ptrs]
    return dict(width=w, height=h, tiles=tiles, size=size, palette=pal,
                pal_ptr=pal_ptr, sections=secs, mcm=m, ptrs=ptrs)


def lz77_encode(data):
    """GBA BIOS LZ77 (codec 0x10). Greedy match, 4 KB window, 18 byte max.

    Used to repack a message block after editing its text. The outer MCM
    section may be re-wrapped with this even where the original was Huffman:
    sections across this ROM use 0x10, 0x24 and 0x30 interchangeably, so the
    reader dispatches on the header byte rather than on the container's codec.
    """
    out = bytearray(struct.pack('<I', 0x10 | (len(data) << 8)))
    i = 0
    while i < len(data):
        flags_at = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= len(data):
                break
            best_len, best_disp = 0, 0
            start = max(0, i - 0x1000)
            window = data[start:i]
            if window:
                for length in range(min(18, len(data) - i), 2, -1):
                    pos = window.rfind(data[i:i + length])
                    if pos != -1:
                        best_len, best_disp = length, i - (start + pos)
                        break
            if best_len >= 3:
                flags |= 0x80 >> bit
                out.append(((best_len - 3) << 4) | ((best_disp - 1) >> 8))
                out.append((best_disp - 1) & 0xFF)
                i += best_len
            else:
                out.append(data[i])
                i += 1
        out[flags_at] = flags
    return bytes(out)
