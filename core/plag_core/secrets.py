"""Secrets come only from Windows Credential Manager (service "PLAG"). Never from files."""

import keyring

SERVICE = "PLAG"


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except Exception:
        pass  # already gone
