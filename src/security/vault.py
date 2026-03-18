"""运行时内存密钥托管。"""

from __future__ import annotations

from typing import Dict, Optional


class SecurityVault:
    """密钥仅保存在当前运行态内存中。"""

    def __init__(self):
        self._keys: Dict[str, str] = {}

    def set_key(self, service_name: str, key_value: str) -> None:
        self._keys[service_name] = key_value

    def get_key(self, service_name: str) -> Optional[str]:
        return self._keys.get(service_name)

    def delete_key(self, service_name: str) -> bool:
        return self._keys.pop(service_name, None) is not None

    def clear(self) -> None:
        self._keys.clear()
