"""Cất API key trong Windows Credential Manager (qua keyring), không ghi ra file."""
from __future__ import annotations

import urllib.request


def open_api_request(request: urllib.request.Request, *, timeout: int):
    """Send credentials to the requested endpoint only, including on urllib redirects."""
    for header in ("Authorization", "X-goog-api-key", "X-api-key"):
        value = request.get_header(header)
        if value is not None:
            request.remove_header(header)
            request.add_unredirected_header(header, value)
    return urllib.request.urlopen(request, timeout=timeout)


SERVICE = "Lilyzono"
OLD_SERVICES = ("EditManga",)  # tên cũ của tool: vẫn đọc được key đã lưu trước khi đổi tên


def get_key(provider: str) -> str:
    try:
        import keyring

        for service in (SERVICE, *OLD_SERVICES):
            key = keyring.get_password(service, provider)
            if key:
                return key
        return ""
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
