"""日志脱敏与物理粉碎。"""

from __future__ import annotations

import os
from pathlib import Path
import re


class LogShredder:
    """负责扫描并抹除日志中的敏感痕迹。"""

    SENSITIVE_PATTERNS = (
        r"sk-[a-zA-Z0-9_-]{20,}",
        r"passwd=[^&\s]+",
        r"password=[^&\s]+",
        r"Bearer\s+[A-Za-z0-9._-]{16,}",
        r"(?:[A-Za-z]:\\Users\\[^\\\s]+\\|/Users/[^/\s]+/)",
    )

    def sanitize_text(self, text: str) -> str:
        sanitized = text or ""
        for pattern in self.SENSITIVE_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
        return sanitized

    def physical_shred(self, file_path: str) -> bool:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return False

        size = path.stat().st_size
        with path.open("r+b") as handle:
            if size > 0:
                handle.write(os.urandom(size))
                handle.flush()
                os.fsync(handle.fileno())
        path.unlink()
        return True
