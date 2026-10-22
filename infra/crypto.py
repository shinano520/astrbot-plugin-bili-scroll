"""Cookie 加密。用随机密钥文件，不依赖机器指纹（跨平台零分支）。"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_FILENAME = "secret.key"
COOKIE_FILENAME = "cookie.enc"


class CryptoError(Exception):
    pass


def load_or_create_key(data_dir: Path) -> bytes:
    """读取或生成密钥。首次生成 32 字节随机，权限 600。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = data_dir / KEY_FILENAME

    if key_path.exists():
        return key_path.read_bytes()

    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)  # Windows 上是 no-op，不报错
    except OSError:
        pass
    return key


def encrypt(plain: str, key: bytes) -> bytes:
    if not plain:
        return b""
    return Fernet(key).encrypt(plain.encode("utf-8"))


def decrypt(blob: bytes, key: bytes) -> str:
    if not blob:
        return ""
    try:
        return Fernet(key).decrypt(blob).decode("utf-8")
    except InvalidToken as e:
        raise CryptoError("密钥不匹配或密文损坏，需要重新导入 Cookie") from e


def save_cookie(data_dir: Path, cookie: str, key: bytes) -> None:
    path = data_dir / COOKIE_FILENAME
    path.write_bytes(encrypt(cookie, key))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_cookie(data_dir: Path, key: bytes) -> str:
    path = data_dir / COOKIE_FILENAME
    if not path.exists():
        return ""
    return decrypt(path.read_bytes(), key)
