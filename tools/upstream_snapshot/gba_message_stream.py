#!/usr/bin/env python3
"""Parse and rewrite the message stream inside a decompressed MCM record.

A record's payload is a sequence of 16-bit fields. Most are commands whose
meaning is still unknown, but `03 00` introduces a text record:

    03 00  <u16 length>  <length bytes: CP932 text, 0x0A line breaks, NUL pad>

The length counts the text bytes *and* the terminating NUL, so
`新しい店長しゃん\\nがやって来た～♪` plus its NUL is 0x22.

Knowing where the length word lives is what makes reinsertion possible:
Korean rarely encodes to exactly the same byte count as the Japanese it
replaces, so the length has to be rewritten along with the text.
"""

from __future__ import annotations

import re

TEXT_TAG = b"\x03\x00"
JP_RE = re.compile(r"[ぁ-ゟァ-ヿ㐀-鿿々〆ー]")
# The game has a tiny extension to CP932 in dialogue payloads.  0x8540 is the
# outline heart used in lines such as ``おいしかったわよ♡``; Python's CP932
# codec quite correctly rejects it, which used to make the whole message look
# like binary data and silently drop it from extraction.
GAME_DECODE_BYTES = {b"\x85\x40": "♡"}
# A few card-effect descriptions embed renderer commands directly inside the
# declared text payload.  They are single-byte control codes, not CP932 text;
# for example 0xD14588 wraps 「人気」 with 0x03 ... 0x1A.  Older extraction
# rejected the whole otherwise-valid message because it allowed only newlines.
# Treat these as zero-width formatting for plain-text extraction/reinsertion.
GAME_INLINE_CONTROLS = frozenset({0x03, 0x1A})


def decode_game_text(raw: bytes) -> str:
    """Decode one message body, including the game's private glyph bytes."""
    out: list[str] = []
    start = 0
    pos = 0
    while pos < len(raw):
        if raw[pos] in GAME_INLINE_CONTROLS:
            if start < pos:
                out.append(raw[start:pos].decode("cp932"))
            pos += 1
            start = pos
            continue
        replacement = GAME_DECODE_BYTES.get(raw[pos:pos + 2])
        if replacement is None:
            pos += 1
            continue
        if start < pos:
            out.append(raw[start:pos].decode("cp932"))
        out.append(replacement)
        pos += 2
        start = pos
    if start < len(raw):
        out.append(raw[start:].decode("cp932"))
    return "".join(out)


class Message:
    __slots__ = ("header", "start", "length", "raw")

    def __init__(self, header: int, start: int, length: int, raw: bytes):
        self.header = header   # offset of the 03 00 tag
        self.start = start     # offset of the first text byte
        self.length = length   # bytes of text including the terminating NUL
        self.raw = raw

    @property
    def text(self) -> str:
        return decode_game_text(self.raw.rstrip(b"\x00"))

    def __repr__(self):
        return f"Message(@{self.header:#x} len={self.length} {self.text!r})"


def _is_text_payload(raw: bytes) -> bool:
    """Reject false headers and desynchronised reads.

    A bare `03 00` also occurs inside binary command payloads, and if a length
    word is misread the walk lands mid-stream and swallows the next record's
    header -- which showed up as messages literally beginning with the bytes
    `03 00 3E 00`. Two checks catch both: a real message body carries no
    control bytes other than its 0x0A line breaks and its trailing NUL
    padding, and it is mostly Japanese once decoded.
    """
    # Real dialogue records are NUL-terminated.  Requiring the terminator is a
    # much stronger binary false-positive guard than the old "50% Japanese"
    # heuristic and lets us keep intentionally punctuation-heavy reactions
    # such as ``にょ～～～～っ！？`` and ``目からビー………………！``.
    if not raw.endswith(b"\x00"):
        return False
    body = raw.rstrip(b"\x00")
    if not body:
        return False
    if any(
        b < 0x20 and b != 0x0A and b not in GAME_INLINE_CONTROLS
        for b in body
    ):
        return False
    try:
        text = decode_game_text(body)
    except UnicodeDecodeError:
        return False
    stripped = text.replace("\n", "")
    if not stripped:
        return False
    jp_count = len(JP_RE.findall(stripped))
    if not jp_count:
        return False
    # Preserve every message the old parser accepted, but also accept strings
    # with at least two Japanese characters even when decorative punctuation
    # makes Japanese less than half of the visible text.  A whole-ROM audit of
    # the newly admitted records yielded only legitimate dialogue/UI strings.
    return jp_count >= 2 or jp_count >= len(stripped) * 0.5


def parse(blob: bytes):
    """Walk the stream two bytes at a time, yielding every text record.

    Walking rather than searching matters: a bare `03 00` also occurs inside
    binary command payloads, and only stepping through the stream in order --
    skipping each text record's declared length -- keeps those from being
    mistaken for headers.
    """
    messages = []
    pos = 0
    end = len(blob)
    while pos + 4 <= end:
        if blob[pos : pos + 2] == TEXT_TAG:
            length = int.from_bytes(blob[pos + 2 : pos + 4], "little")
            start = pos + 4
            if 0 < length <= 512 and start + length <= end:
                raw = blob[start : start + length]
                if _is_text_payload(raw):
                    messages.append(Message(pos, start, length, raw))
                    pos = start + length
                    continue
        pos += 2
    return messages


def pad_payload(text: bytes, like: "Message | None" = None,
                preserve_length: bool = False) -> bytes:
    """Terminate and pad `text` the way the original records do.

    Records do not all use a single trailing NUL -- some carry two, and the
    stream stays 2-byte aligned throughout. Reproducing the original's own
    padding width keeps an unchanged message byte-for-byte identical, which is
    what makes a no-op rebuild verifiable.
    """
    nulls = 1
    if like is not None:
        original = len(like.raw) - len(like.raw.rstrip(b"\x00"))
        nulls = max(1, original)
    payload = text + b"\x00" * nulls
    if len(payload) % 2:
        payload += b"\x00"
    if preserve_length:
        if like is None:
            raise ValueError("preserve_length requires an original message")
        if len(payload) > like.length:
            raise ValueError(
                f"replacement needs {len(payload)} bytes but fixed slot is {like.length}"
            )
        payload += b"\x00" * (like.length - len(payload))
    return payload


def rebuild(blob: bytes, replacements: dict[int, bytes]) -> bytes:
    """Return a new payload with the given messages replaced.

    `replacements` maps a message's header offset to its complete new payload,
    terminator and padding included -- build it with `pad_payload`. Each
    record's length word is rewritten, so a replacement may differ in size.
    """
    messages = {m.header: m for m in parse(blob)}
    out = bytearray()
    pos = 0
    for header in sorted(replacements):
        message = messages.get(header)
        if message is None:
            raise KeyError(f"no text record at {header:#x}")
        payload = replacements[header]
        out += blob[pos : message.header]
        out += TEXT_TAG + len(payload).to_bytes(2, "little") + payload
        pos = message.start + message.length
    out += blob[pos:]

    return bytes(out)
