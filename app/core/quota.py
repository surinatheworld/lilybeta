"""Key người dùng và hạn mức token (như các dịch vụ AI): chủ tool cấp key, tool trừ token mỗi lần làm việc.

- Key do chủ tool tạo bằng tools/make_key.py, ký bằng khoá bí mật Ed25519 (chỉ chủ tool giữ); tool chỉ có khoá công
  khai nên không ai tự tạo / sửa được key. Key ghi: mã người dùng, tên, tổng token được cấp (None = không giới hạn),
  hạn dùng.
- Nạp thêm: chủ tool cấp key mới cùng mã người dùng với tổng token lớn hơn; số đã dùng tính theo mã người dùng.
- Số token đã dùng lưu ở %APPDATA%/Lilyzono và registry (lấy số lớn hơn), có mã kiểm tra chống sửa tay.
  Đây là kiểm soát offline: chặn được người dùng bình thường, người rành máy vẫn có thể xoá dấu vết để lách.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache

PUBLIC_KEY = "5c842a4dec7fd4e5ec6878b6acb62992adef53b346f962f2e2ccf8a02bd1c30e"  # hex, ghi bởi `python tools/make_key.py init`
PAGE_COST = 10  # tự ghép thoại + xoá chữ + type một trang (app hoặc Photoshop)
REGION_COST = 1  # xử lý tay một khung chữ
AI_COST = 5  # một lần gọi AI trên mạng vẽ lại nền (Gemini / OpenAI)
PREFIX = "LZ1-"


class TokenError(ValueError):
    """Chưa có key / key sai / hết hạn / hết token."""


@dataclass(frozen=True)
class License:
    user: str
    name: str
    tokens: int | None  # tổng token được cấp; None = không giới hạn
    expires: str | None  # "YYYY-MM-DD" (hết ngày này thì hết hạn) hoặc None
    issued: str


# --- Ed25519 (RFC 8032, bản tham chiếu): không cần thư viện ngoài --------------------------------------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return E * F % _P, G * H % _P, F * G % _P, E * H % _P


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _same(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(pt) -> bytes:
    z = pow(pt[2], _P - 2, _P)
    x, y = pt[0] * z % _P, pt[1] * z % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _expand(secret: bytes):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    return _compress(_mul(_expand(secret)[0], _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    big_r = _compress(_mul(r, _G))
    h = int.from_bytes(hashlib.sha512(big_r + pub + msg).digest(), "little") % _L
    return big_r + int.to_bytes((r + h * a) % _L, 32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    a, r = _decompress(pub), _decompress(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
    return _same(_mul(s, _G), _add(r, _mul(h, a)))


# --- Key -----------------------------------------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_key(secret: bytes, lic: License) -> str:
    payload = json.dumps({"u": lic.user, "n": lic.name, "t": lic.tokens, "e": lic.expires, "i": lic.issued},
                         ensure_ascii=False, separators=(",", ":")).encode()
    return PREFIX + _b64(payload) + "." + _b64(sign(secret, payload))


@lru_cache(maxsize=8)
def parse_key(text: str, pub_hex: str | None = None) -> License:
    """Đọc và kiểm chữ ký của key; sai thì TokenError."""
    text = "".join(text.split())
    pub = bytes.fromhex(pub_hex if pub_hex is not None else PUBLIC_KEY)
    try:
        if not text.startswith(PREFIX):
            raise ValueError
        body, sig = text[len(PREFIX):].split(".")
        payload, sig = _unb64(body), _unb64(sig)
        if not verify(pub, payload, sig):
            raise ValueError
        d = json.loads(payload)
        return License(str(d["u"]), str(d.get("n", "")), d.get("t"), d.get("e"), str(d.get("i", "")))
    except (ValueError, KeyError, TypeError):
        raise TokenError("Key không hợp lệ (sai hoặc chép thiếu ký tự).") from None


# --- Lưu key và sổ token trên máy ----------------------------------------------------------------------------
def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Lilyzono")


def _key_file() -> str:
    return os.path.join(data_dir(), "license.key")


def _ledger_file() -> str:
    return os.path.join(data_dir(), "usage.json")


def unlimited() -> bool:
    """Chạy từ mã nguồn với LILYZONO_UNLIMITED=1 (kiểm thử, công cụ đo): không trừ token. Bản .exe không bỏ qua."""
    return not getattr(sys, "frozen", False) and os.environ.get("LILYZONO_UNLIMITED") == "1"


def installed() -> License | None:
    try:
        with open(_key_file(), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    try:
        return parse_key(text.strip())
    except TokenError:
        return None


def install(text: str) -> License:
    """Kiểm key rồi lưu lên máy (thay key cũ). Trả về thông tin key."""
    lic = parse_key("".join(text.split()))
    os.makedirs(data_dir(), exist_ok=True)
    with open(_key_file(), "w", encoding="utf-8") as f:
        f.write("".join(text.split()))
    return lic


def _mac(user: str, spent: int, seen: int) -> str:
    return hmac.new(hashlib.sha256(b"lilyzono-usage:" + PUBLIC_KEY.encode()).digest(),
                    f"{user}:{spent}:{seen}".encode(), hashlib.sha256).hexdigest()[:24]


def _decode(entry) -> tuple[int, int]:
    """(đã dùng, mốc thời gian lớn nhất đã thấy) từ một bản ghi; bản ghi bị sửa tay thì coi như không có."""
    try:
        spent, seen, mac = int(entry["s"]), int(entry["t"]), entry["m"]
    except (TypeError, KeyError, ValueError):
        return 0, 0
    return (spent, seen) if hmac.compare_digest(mac, _mac(entry.get("u", ""), spent, seen)) else (0, 0)


def _read_file() -> dict:
    try:
        with open(_ledger_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _registry(user: str, value: str | None = None):
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Lilyzono\Usage") as k:
            if value is None:
                return json.loads(winreg.QueryValueEx(k, user)[0])
            winreg.SetValueEx(k, user, 0, winreg.REG_SZ, value)
    except (OSError, ValueError):
        return None
    return None


def usage(user: str) -> tuple[int, int]:
    """(token đã dùng, mốc thời gian lớn nhất đã thấy) của một mã người dùng."""
    a = _decode(_read_file().get(user))
    b = _decode(_registry(user))
    return max(a[0], b[0]), max(a[1], b[1])


def _record(user: str, spent: int, seen: int) -> None:
    entry = {"u": user, "s": spent, "t": seen, "m": _mac(user, spent, seen)}
    data = _read_file()
    data[user] = entry
    os.makedirs(data_dir(), exist_ok=True)
    tmp = _ledger_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _ledger_file())
    _registry(user, json.dumps(entry))


def _now() -> int:
    return int(datetime.datetime.now().timestamp())


def status() -> tuple[License | None, int | None]:
    """(key đang dùng, token còn lại – None nếu không giới hạn hoặc chưa có key)."""
    lic = installed()
    if lic is None or lic.tokens is None:
        return lic, None
    return lic, max(0, lic.tokens - usage(lic.user)[0])


def describe() -> str:
    """Một dòng mô tả cho thanh trạng thái / hộp thoại."""
    if unlimited():
        return "Token: không giới hạn (chế độ phát triển)"
    lic, left = status()
    if lic is None:
        return "Chưa có key"
    out = f"{lic.name or lic.user} · " + ("không giới hạn" if lic.tokens is None else f"còn {left:,}/{lic.tokens:,} token")
    return out + (f" · hạn {lic.expires}" if lic.expires else "")


def check(n: int) -> None:
    """Báo lỗi nếu không đủ `n` token (không trừ)."""
    _use(n, record=False)


def charge(n: int) -> None:
    """Trừ `n` token trước khi làm việc; không đủ thì TokenError (không trừ gì)."""
    _use(n, record=True)


def _use(n: int, record: bool) -> None:
    if unlimited() or n <= 0:
        return
    lic = installed()
    if lic is None:
        raise TokenError("Chưa có key Lilyzono (xin key từ người cấp tool).")
    spent, seen = usage(lic.user)
    now = _now()
    if lic.expires:
        if now < seen - 86400:
            raise TokenError("Giờ máy bị chỉnh lùi: chỉnh lại ngày giờ đúng để tiếp tục.")
        if datetime.date.today().isoformat() > lic.expires:
            raise TokenError(f"Key đã hết hạn ngày {lic.expires}. Xin key mới để tiếp tục.")
    if lic.tokens is not None and spent + n > lic.tokens:
        left = max(0, lic.tokens - spent)
        raise TokenError(f"Hết token: còn {left}, cần {n}. Xin key nạp thêm để tiếp tục.")
    if record:
        _record(lic.user, spent + n, max(seen, now))
