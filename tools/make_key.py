"""Cấp key Lilyzono cho người dùng (chỉ chủ tool dùng).

Lần đầu (một lần duy nhất, tạo khoá bí mật ở %USERPROFILE%/.lilyzono và ghi khoá công khai vào app/core/quota.py):
    python tools/make_key.py init

Cấp key:
    python tools/make_key.py issue "Tên người dùng" 1000              # 1000 token, không hạn
    python tools/make_key.py issue "Tên người dùng" 1000 --days 30    # hết hạn sau 30 ngày
    python tools/make_key.py issue "Tên" 3000 --user u-ab12cd         # nạp thêm: cùng mã người dùng, tổng 3000 token
    python tools/make_key.py issue "Chủ tool" --unlimited             # không giới hạn

Xem một key:  python tools/make_key.py show <key>
Danh sách đã cấp: python tools/make_key.py list

Khoá bí mật (owner_secret.key) KHÔNG được đưa lên git; mất nó thì không cấp được key mới cho bản đã phát hành –
hãy sao lưu thư mục %USERPROFILE%/.lilyzono.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import quota  # noqa: E402

OWNER_DIR = os.path.join(os.path.expanduser("~"), ".lilyzono")
SECRET_FILE = os.path.join(OWNER_DIR, "owner_secret.key")
ISSUED_FILE = os.path.join(OWNER_DIR, "issued.csv")
QUOTA_SOURCE = os.path.join(os.path.dirname(__file__), "..", "app", "core", "quota.py")


def load_secret() -> bytes:
    try:
        with open(SECRET_FILE, encoding="utf-8") as f:
            secret = bytes.fromhex(f.read().strip())
    except OSError:
        sys.exit(f"Chưa có khoá bí mật ({SECRET_FILE}). Chạy: python tools/make_key.py init")
    if quota.public_key(secret).hex() != quota.PUBLIC_KEY:
        sys.exit("Khoá bí mật không khớp khoá công khai trong app/core/quota.py.")
    return secret


def init(_args) -> None:
    if os.path.exists(SECRET_FILE):
        sys.exit(f"Đã có khoá bí mật ở {SECRET_FILE} – không tạo lại (key đã cấp sẽ mất hiệu lực).")
    secret = secrets.token_bytes(32)
    os.makedirs(OWNER_DIR, exist_ok=True)
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(secret.hex())
    pub = quota.public_key(secret).hex()
    with open(QUOTA_SOURCE, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r'^PUBLIC_KEY = ".*?"', f'PUBLIC_KEY = "{pub}"', src, count=1, flags=re.M)
    with open(QUOTA_SOURCE, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"Khoá bí mật: {SECRET_FILE}  (sao lưu, không chia sẻ, không đưa lên git)")
    print(f"Khoá công khai đã ghi vào app/core/quota.py: {pub}")


def issue(args) -> None:
    secret = load_secret()
    if not args.unlimited and args.tokens is None:
        sys.exit("Nhập số token (hoặc --unlimited).")
    today = datetime.date.today()
    lic = quota.License(
        user=args.user or "u-" + secrets.token_hex(3),
        name=args.name,
        tokens=None if args.unlimited else int(args.tokens),
        expires=(today + datetime.timedelta(days=args.days)).isoformat() if args.days else None,
        issued=today.isoformat(),
    )
    key = quota.make_key(secret, lic)
    new = not os.path.exists(ISSUED_FILE)
    with open(ISSUED_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ngày cấp", "mã người dùng", "tên", "tổng token", "hạn", "key"])
        w.writerow([lic.issued, lic.user, lic.name, "không giới hạn" if lic.tokens is None else lic.tokens,
                    lic.expires or "", key])
    print(f"Người dùng: {lic.name}  ·  mã {lic.user}  ·  "
          f"{'không giới hạn' if lic.tokens is None else f'{lic.tokens} token'}"
          f"{'  ·  hạn ' + lic.expires if lic.expires else ''}")
    print(key)


def show(args) -> None:
    lic = quota.parse_key(args.key)
    print(lic)


def list_issued(_args) -> None:
    try:
        with open(ISSUED_FILE, encoding="utf-8") as f:
            for row in csv.reader(f):
                print(" | ".join(row[:5]))
    except OSError:
        print("Chưa cấp key nào.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Cấp key Lilyzono")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=init)
    s = sub.add_parser("issue")
    s.add_argument("name")
    s.add_argument("tokens", nargs="?", type=int)
    s.add_argument("--days", type=int, default=0, help="số ngày tới khi hết hạn (0 = không hạn)")
    s.add_argument("--user", help="mã người dùng có sẵn (nạp thêm token cho người đó)")
    s.add_argument("--unlimited", action="store_true")
    s.set_defaults(fn=issue)
    s = sub.add_parser("show")
    s.add_argument("key")
    s.set_defaults(fn=show)
    sub.add_parser("list").set_defaults(fn=list_issued)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
