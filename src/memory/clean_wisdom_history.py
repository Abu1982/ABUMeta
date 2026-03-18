"""离线清洗历史智慧层数据。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory import MemoryManager
from src.utils.map_exporter import _normalize_memory_summary


_ACTIVITY_MARKERS = ("自主巡航", "基因阻断", "生产心跳", "巡航任务")
_WISDOM_REWRITE_MAP = {
    "交易未稳，先保现金。": "谋定后动。",
    "日志见根，优化立成。": "见微知著。",
    "显存临界，削峰保速。": "未雨绸缪。",
    "兼收并蓄，制度先于热情。": "兼收并蓄。",
    "大道至简，少即是稳。": "大道至简。",
}


def _normalize_wisdom_text(text: str) -> str:
    normalized = (
        _normalize_memory_summary(text).replace("经验：", "").replace("分析：", "")
    )
    normalized = normalized.strip("。；， ")
    if normalized in _WISDOM_REWRITE_MAP:
        return _WISDOM_REWRITE_MAP[normalized]
    if len(normalized) > 40:
        normalized = normalized[:40].rstrip("，；。,. ")
    if normalized and normalized[-1] not in "。！？!?":
        normalized += "。"
    return normalized


def main() -> None:
    memory_manager = MemoryManager()
    wisdom_entries = memory_manager.db_manager.list_semantic_wisdom(limit=100000)

    rewritten = 0
    deleted = 0
    seen = set()
    for wisdom in wisdom_entries:
        text = wisdom.wisdom_text or ""
        normalized = _normalize_wisdom_text(text)

        if any(marker in normalized for marker in _ACTIVITY_MARKERS):
            if memory_manager.db_manager.soft_delete_semantic_wisdom(int(wisdom.id)):
                deleted += 1
            continue

        dedupe_key = (wisdom.category or "learning", normalized)
        if dedupe_key in seen:
            if memory_manager.db_manager.soft_delete_semantic_wisdom(int(wisdom.id)):
                deleted += 1
            continue
        seen.add(dedupe_key)

        if normalized != text:
            memory_manager.db_manager.update_semantic_wisdom(
                int(wisdom.id), wisdom_text=normalized
            )
            rewritten += 1

    print(json.dumps({"rewritten": rewritten, "deleted": deleted}, ensure_ascii=False))


if __name__ == "__main__":
    main()
