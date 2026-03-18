"""原始冷库月度整理脚本。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory import MemoryManager


def main() -> None:
    manager = MemoryManager()
    stats = manager.rebuild_raw_archive_monthly_summary()
    summaries = manager.list_raw_archive_monthly_summaries()
    print(
        json.dumps(
            {
                "months": stats["months"],
                "summaries": summaries,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
