"""An AES-encrypted PDF, as NTSB publishes, built without the `cryptography` package.

pypdf cannot write (or read) AES without `cryptography`, which Telltale does not install, so the
encrypted-PDF fallback in `telltale.ingest.extract` is tested with a file built here: a small
FIPS-197 AES-128 and the PDF standard security handler's RC4/MD5 key derivation.
"""

import hashlib
from pathlib import Path


def _xtime(b: int) -> int:
    return ((b << 1) ^ 0x1B) & 0xFF if b & 0x80 else b << 1


def _gf_mul(a: int, b: int) -> int:
    product = 0
    while b:
        if b & 1:
            product ^= a
        a, b = _xtime(a), b >> 1
    return product


def _sbox() -> list[int]:
    box = []
    for x in range(256):
        inverse = next((y for y in range(1, 256) if _gf_mul(x, y) == 1), 0)
        s = inverse
        for shift in range(1, 5):
            s ^= ((inverse << shift) | (inverse >> (8 - shift))) & 0xFF
        box.append(s ^ 0x63)
    return box


_SBOX = _sbox()


def _aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    """FIPS-197 AES-128 on one 16-byte block (state as a flat column-major list)."""
    words = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        word = list(words[i - 1])
        if i % 4 == 0:
            word = [_SBOX[b] for b in word[1:] + word[:1]]
            word[0] ^= rcon
            rcon = _xtime(rcon)
        words.append([a ^ b for a, b in zip(words[i - 4], word)])
    round_keys = [sum(words[r * 4 : r * 4 + 4], []) for r in range(11)]

    state = [b ^ k for b, k in zip(block, round_keys[0])]
    for round_number in range(1, 11):
        state = [_SBOX[b] for b in state]
        state = [state[(i + 4 * (i % 4)) % 16] for i in range(16)]  # ShiftRows
        if round_number != 10:
            mixed = []
            for c in range(4):
                a = state[c * 4 : c * 4 + 4]
                mixed += [
                    _xtime(a[0]) ^ _xtime(a[1]) ^ a[1] ^ a[2] ^ a[3],
                    a[0] ^ _xtime(a[1]) ^ _xtime(a[2]) ^ a[2] ^ a[3],
                    a[0] ^ a[1] ^ _xtime(a[2]) ^ _xtime(a[3]) ^ a[3],
                    _xtime(a[0]) ^ a[0] ^ a[1] ^ a[2] ^ _xtime(a[3]),
                ]
            state = mixed
        state = [b ^ k for b, k in zip(state, round_keys[round_number])]
    return bytes(state)


def _aes128_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    out, previous = b"", iv
    for i in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[i : i + 16], previous))
        previous = _aes128_encrypt_block(key, block)
        out += previous
    return out


def _rc4(key: bytes, data: bytes) -> bytes:
    s, j = list(range(256)), 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out, i, j = bytearray(), 0, 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


_PASSWORD_PAD = bytes.fromhex(
    "28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A"
)


def write_aes_encrypted_pdf(path: Path, text: str) -> None:
    """A one-page PDF showing `text`, AES-128 encrypted (PDF 1.6, V4 R4, AESV2) with an empty
    user password: it opens without a password, as NTSB report PDFs do."""
    md5 = lambda data: hashlib.md5(data).digest()  # noqa: E731
    file_id = bytes(range(16))
    permissions = (-4).to_bytes(4, "little", signed=True)

    # PDF 32000-1 section 7.6.3.3, algorithms 2, 3 and 5; owner password "owner".
    owner_key = md5(b"owner" + _PASSWORD_PAD[:27])
    for _ in range(50):
        owner_key = md5(owner_key)
    owner_entry = _rc4(owner_key, _PASSWORD_PAD)
    for i in range(1, 20):
        owner_entry = _rc4(bytes(b ^ i for b in owner_key), owner_entry)
    file_key = md5(_PASSWORD_PAD + owner_entry + permissions + file_id)
    for _ in range(50):
        file_key = md5(file_key)
    user_entry = _rc4(file_key, md5(_PASSWORD_PAD + file_id))
    for i in range(1, 20):
        user_entry = _rc4(bytes(b ^ i for b in file_key), user_entry)
    user_entry += bytes(16)

    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 20 100 Td ({escaped}) Tj ET".encode("latin-1")
    object_key = md5(file_key + (4).to_bytes(3, "little") + (0).to_bytes(2, "little") + b"sAlT")
    iv = bytes(range(16, 32))
    stream = iv + _aes128_cbc_encrypt(object_key, iv, content)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Filter /Standard /V 4 /R 4 /Length 128 /P -4 "
        b"/CF << /StdCF << /AuthEvent /DocOpen /CFM /AESV2 /Length 16 >> >> "
        b"/StmF /StdCF /StrF /StdCF /O <" + owner_entry.hex().encode() + b"> /U <"
        + user_entry.hex().encode() + b"> >>",
    ]
    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Encrypt 6 0 R /ID [<%s> <%s>] >>\n" % (
        len(objects) + 1, file_id.hex().encode(), file_id.hex().encode()
    )
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(bytes(out))
