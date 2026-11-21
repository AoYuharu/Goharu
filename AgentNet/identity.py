"""AgentNet 身份：ed25519 密钥对即身份本体。

密钥文件：`{dir}/identity.key`（私钥，0600）+ `agent_id` 侧车文件。
agent_id = "agt_" + sha256(公钥 DER)[:16]——公钥指纹即身份，白名单按它登记。
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .protocol import agent_id_from_pubkey_der

logger = logging.getLogger(__name__)


class Identity:
    def __init__(self, priv: Ed25519PrivateKey, agent_id: str, name: str = ""):
        self._priv = priv
        self.agent_id = agent_id
        self.name = name or agent_id

    # ---------------------------------------------------------- 构造/加载
    @classmethod
    def load_or_create(cls, dir_path: str | Path, name: str = "") -> "Identity":
        """从目录加载身份；不存在则生成并落盘。"""
        d = Path(dir_path)
        d.mkdir(parents=True, exist_ok=True)
        key_path = d / "identity.key"
        if key_path.exists():
            priv = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            pub_der = _pub_der(priv.public_key())
            ident = cls(priv, agent_id_from_pubkey_der(pub_der), name)  # type: ignore[arg-type]
            logger.info("identity 加载: %s (%s)", ident.agent_id, d)
            return ident
        priv = Ed25519PrivateKey.generate()
        pub_der = _pub_der(priv.public_key())
        key_path.write_bytes(
            priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass  # Windows 无 POSIX 权限位
        ident = cls(priv, agent_id_from_pubkey_der(pub_der), name)
        (d / "agent_id").write_text(ident.agent_id, encoding="utf-8")
        logger.info("identity 生成: %s (%s)", ident.agent_id, d)
        return ident

    # ---------------------------------------------------------- 公钥导出
    @property
    def pubkey_b64(self) -> str:
        return base64.b64encode(_pub_der(self._priv.public_key())).decode("ascii")

    # ---------------------------------------------------------- 签名
    def sign(self, data: bytes) -> str:
        return base64.b64encode(self._priv.sign(data)).decode("ascii")

    @staticmethod
    def verify(pubkey_b64: str, data: bytes, sig_b64: str) -> bool:
        try:
            pub = serialization.load_der_public_key(base64.b64decode(pubkey_b64))
            assert isinstance(pub, Ed25519PublicKey)
            pub.verify(base64.b64decode(sig_b64), data)
            return True
        except Exception:
            return False


def _pub_der(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
