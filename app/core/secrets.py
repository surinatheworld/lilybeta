"""Cất API key trong Windows Credential Manager (qua keyring), không ghi ra file."""
from __future__ import annotations

SERVICE = "EditManga"


def get_key(provider: str) -> str:
    try:
        import keyring

        return keyring.get_password(SERVICE, provider) or ""
    except Exception:  # noqa: BLE001 - máy không có két mật khẩu thì coi như chưa lưu
        return ""


def set_key(provider: str, key: str) -> None:
    import keyring

    if key:
        keyring.set_password(SERVICE, provider, key)
    else:
        try:
            keyring.delete_password(SERVICE, provider)
        except Exception:  # noqa: BLE001 - chưa có key thì thôi
            pass
