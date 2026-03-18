"""语言掩码实现。"""

from __future__ import annotations

import re
from typing import Iterable, Sequence


class LanguageMask:
    """对最终文本执行违禁词清洗与社交掩码。"""

    DEFAULT_FORBIDDEN_PHRASES: Sequence[str] = (
        "作为AI助手",
        "AI语言模型",
    )
    SAFE_FALLBACK = "……"
    _CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```")

    def __init__(self, forbidden_phrases: Iterable[str] | None = None):
        phrases = forbidden_phrases if forbidden_phrases is not None else self.DEFAULT_FORBIDDEN_PHRASES
        self.forbidden_phrases = tuple(phrase for phrase in phrases if phrase)

    def sanitize_text(self, text: str) -> str:
        """清洗自然语言文本中的违禁词，并保留代码块原样。"""
        if not text or not text.strip():
            return self.SAFE_FALLBACK

        parts: list[str] = []
        last_index = 0
        for match in self._CODE_BLOCK_PATTERN.finditer(text):
            plain_segment = text[last_index:match.start()]
            parts.append(self._sanitize_plain_text_segment(plain_segment))
            parts.append(match.group(0))
            last_index = match.end()

        parts.append(self._sanitize_plain_text_segment(text[last_index:]))
        sanitized = "".join(parts).strip()
        return sanitized or self.SAFE_FALLBACK

    def apply_social_mask(self, text: str, anxiety: float) -> str:
        """根据焦虑值对文本表达形态做确定性调整。"""
        normalized_anxiety = self._clamp(anxiety)
        if not text or not text.strip():
            return self.SAFE_FALLBACK

        if normalized_anxiety < 0.3:
            return text

        if normalized_anxiety < 0.8:
            return f"嗯，{text}"

        compact = text.strip()
        compact = re.sub(r"[。！？!?]+$", "", compact)
        return f"呃，{compact}……"

    def mask_response(self, text: str, anxiety: float) -> str:
        """先清洗，再按焦虑值施加社交掩码。"""
        sanitized = self.sanitize_text(text)
        return self.apply_social_mask(sanitized, anxiety)

    def _sanitize_plain_text_segment(self, text: str) -> str:
        sanitized = text
        for phrase in self.forbidden_phrases:
            sanitized = sanitized.replace(phrase, "")

        sanitized = re.sub(r"\s+", " ", sanitized)
        sanitized = re.sub(r"([，。！？；：、])\1+", r"\1", sanitized)
        sanitized = re.sub(r"(^|\s)[，。！？；：、]+", r"\1", sanitized)
        sanitized = re.sub(r"[，。！？；：、]+$", "", sanitized)
        return sanitized.strip()

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
