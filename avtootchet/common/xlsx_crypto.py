"""Шифрование отчёта паролем средствами формата Excel (ECMA-376 Agile, AES-256).

Такой файл открывается двойным щелчком в Excel: Excel сам спросит пароль.
Без пароля содержимое прочитать нельзя.
"""
from __future__ import annotations

from io import BytesIO

MIN_PASSWORD = 6


class ReportPasswordError(Exception):
    pass


def check_password(password: str) -> None:
    if not password:
        raise ReportPasswordError("Задайте пароль для отчётов: «⚙ Настройки» → «Пароль для отчётов». "
                                  "Без пароля программа отчёт не сохраняет.")
    if len(password) < MIN_PASSWORD:
        raise ReportPasswordError(f"Пароль для отчётов слишком короткий: нужно не меньше "
                                  f"{MIN_PASSWORD} символов.")


def encrypt_xlsx(data: bytes, password: str) -> bytes:
    """Зашифровать .xlsx паролем и сразу проверить, что он расшифровывается обратно."""
    import msoffcrypto  # noqa: PLC0415
    from msoffcrypto.format.ooxml import OOXMLFile  # noqa: PLC0415

    check_password(password)
    out = BytesIO()
    OOXMLFile(BytesIO(data)).encrypt(password, out)
    enc = out.getvalue()
    check = msoffcrypto.OfficeFile(BytesIO(enc))
    check.load_key(password=password)
    back = BytesIO()
    check.decrypt(back)
    if back.getvalue() != data:
        raise ReportPasswordError("Не удалось зашифровать отчёт: проверка расшифровки не прошла.")
    return enc


def decrypt_xlsx(data: bytes, password: str) -> bytes:
    import msoffcrypto  # noqa: PLC0415
    f = msoffcrypto.OfficeFile(BytesIO(data))
    f.load_key(password=password)
    out = BytesIO()
    f.decrypt(out)
    return out.getvalue()


def is_encrypted(data: bytes) -> bool:
    import msoffcrypto  # noqa: PLC0415
    try:
        return bool(msoffcrypto.OfficeFile(BytesIO(data)).is_encrypted())
    except Exception:  # noqa: BLE001 - обычный .xlsx (zip) - не зашифрован
        return False
