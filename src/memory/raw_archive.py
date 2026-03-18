"""原始记忆冷库管理器。"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.constants import RAW_ARCHIVE_DB_PATH
from config.settings import settings
from src.utils.logger import log


class RawArchiveManager:
    """独立于工作记忆库的原始记忆冷库。"""

    def __init__(self, archive_path: Optional[str] = None):
        configured = (
            archive_path or os.getenv("ABU_RAW_ARCHIVE_DB_PATH") or RAW_ARCHIVE_DB_PATH
        )
        self.archive_path = Path(configured)
        if not self.archive_path.is_absolute():
            self.archive_path = Path(settings.BASE_DIR) / self.archive_path
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.archive_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        return conn

    @staticmethod
    def _active_record_clause() -> str:
        return "COALESCE(verification_status, 'unverified') != 'deprecated'"

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_archive_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    archive_month TEXT NOT NULL,
                    archive_seq INTEGER NOT NULL,
                    memory_entry_id INTEGER,
                    source_type TEXT,
                    source_url TEXT,
                    source_reputation REAL,
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    permanence INTEGER NOT NULL DEFAULT 1,
                    raw_event TEXT NOT NULL,
                    raw_thought TEXT,
                    raw_lesson TEXT,
                    raw_emotion TEXT,
                    raw_payload TEXT
                )
                """
            )
            self._migrate_schema_v2(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_archive_month_seq ON raw_archive_entries (archive_month, archive_seq)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_archive_memory_entry ON raw_archive_entries (memory_entry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_archive_created_at ON raw_archive_entries (created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_archive_monthly_summary (
                    archive_month TEXT PRIMARY KEY,
                    start_seq INTEGER NOT NULL,
                    end_seq INTEGER NOT NULL,
                    record_count INTEGER NOT NULL,
                    first_created_at TEXT,
                    last_created_at TEXT,
                    source_type_breakdown TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
        log.info("🧊 原始记忆冷库已初始化: {}", self.archive_path)

    def _migrate_schema_v2(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(raw_archive_entries)").fetchall()
        }
        migrations = {
            "full_text": "ALTER TABLE raw_archive_entries ADD COLUMN full_text TEXT",
            "raw_source_data": "ALTER TABLE raw_archive_entries ADD COLUMN raw_source_data BLOB",
            "source_encoding": "ALTER TABLE raw_archive_entries ADD COLUMN source_encoding TEXT",
            "source_content_type": "ALTER TABLE raw_archive_entries ADD COLUMN source_content_type TEXT",
        }
        for column, statement in migrations.items():
            if column in columns:
                continue
            conn.execute(statement)

    def create_entry(
        self,
        *,
        event: str,
        thought: Optional[str],
        lesson: Optional[str],
        emotion: Optional[Dict[str, float]],
        source_type: str = "unknown",
        source_url: Optional[str] = None,
        source_reputation: Optional[float] = None,
        verification_status: str = "auto",
        permanence: bool = True,
        raw_payload: Optional[Dict[str, Any]] = None,
        full_text: Optional[str] = None,
        raw_source_data: Optional[Any] = None,
        source_encoding: Optional[str] = None,
        source_content_type: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        created = created_at or datetime.now()
        archive_month = created.strftime("%Y-%m")
        resolved_verification = self._resolve_verification_status(
            verification_status=verification_status,
            source_type=source_type,
            source_reputation=source_reputation,
            raw_payload=raw_payload,
        )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(archive_seq), 0) FROM raw_archive_entries WHERE archive_month = ?",
                (archive_month,),
            ).fetchone()
            archive_seq = int(row[0]) + 1
            cursor = conn.execute(
                """
                INSERT INTO raw_archive_entries (
                    created_at, archive_month, archive_seq, memory_entry_id, source_type, source_url,
                    source_reputation, verification_status, permanence, raw_event, raw_thought,
                    raw_lesson, raw_emotion, raw_payload, full_text, raw_source_data,
                    source_encoding, source_content_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created.isoformat(),
                    archive_month,
                    archive_seq,
                    None,
                    source_type,
                    source_url,
                    source_reputation,
                    resolved_verification,
                    1 if permanence else 0,
                    event,
                    thought,
                    lesson,
                    json.dumps(emotion, ensure_ascii=False)
                    if emotion is not None
                    else None,
                    json.dumps(raw_payload, ensure_ascii=False)
                    if raw_payload is not None
                    else None,
                    full_text,
                    self._serialize_raw_source_data(raw_source_data),
                    source_encoding,
                    source_content_type,
                ),
            )
            row_id = cursor.lastrowid
            if row_id is None:
                raise RuntimeError("raw archive insert did not return row id")
            archive_id = int(row_id)
        return {
            "id": archive_id,
            "created_at": created.isoformat(),
            "archive_month": archive_month,
            "archive_seq": archive_seq,
            "verification_status": resolved_verification,
        }

    def _serialize_raw_source_data(
        self, raw_source_data: Optional[Any]
    ) -> Optional[Any]:
        if raw_source_data is None:
            return None
        if isinstance(raw_source_data, str):
            return raw_source_data
        if isinstance(raw_source_data, (bytes, bytearray, memoryview)):
            return sqlite3.Binary(bytes(raw_source_data))
        if isinstance(raw_source_data, (dict, list)):
            return json.dumps(raw_source_data, ensure_ascii=False)
        return str(raw_source_data)

    def _resolve_verification_status(
        self,
        *,
        verification_status: str,
        source_type: str,
        source_reputation: Optional[float],
        raw_payload: Optional[Dict[str, Any]],
    ) -> str:
        if verification_status and verification_status != "auto":
            return verification_status

        payload = raw_payload or {}
        if payload.get("source_conflicts"):
            return "conflicted"
        normalized_type = (source_type or "unknown").lower()
        if normalized_type in {"system", "backtrack_resolution", "dialogue_manual"}:
            return "trusted"
        if source_reputation is None:
            return "unverified"
        if source_reputation >= 0.9:
            return "trusted"
        if source_reputation >= 0.75:
            return "provisional"
        if source_reputation >= 0.45:
            return "unverified"
        return "conflicted"

    def bind_memory_entry(self, archive_id: int, memory_entry_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE raw_archive_entries SET memory_entry_id = ? WHERE id = ?",
                (memory_entry_id, archive_id),
            )

    def mark_deprecated_empty_web_records(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE raw_archive_entries
                SET verification_status = 'deprecated'
                WHERE source_type = 'web'
                  AND COALESCE(length(full_text), 0) = 0
                  AND COALESCE(length(raw_source_data), 0) = 0
                  AND {self._active_record_clause()}
                """
            )
            return int(cursor.rowcount or 0)

    def get_storage_health(self) -> Dict[str, Any]:
        with self._connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
            wal_checkpoint = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            wal_autocheckpoint = conn.execute("PRAGMA wal_autocheckpoint").fetchone()
        wal_path = Path(f"{self.archive_path}-wal")
        return {
            "journal_mode": journal_mode[0] if journal_mode else None,
            "wal_autocheckpoint": int(wal_autocheckpoint[0])
            if wal_autocheckpoint
            else None,
            "wal_checkpoint": {
                "busy": int(wal_checkpoint[0]),
                "log_frames": int(wal_checkpoint[1]),
                "checkpointed_frames": int(wal_checkpoint[2]),
            }
            if wal_checkpoint
            else None,
            "db_size_bytes": self.archive_path.stat().st_size
            if self.archive_path.exists()
            else 0,
            "wal_size_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
        }

    def delete_by_memory_entry_id(self, memory_entry_id: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM raw_archive_entries WHERE memory_entry_id = ?",
                (memory_entry_id,),
            )
            return int(cursor.rowcount or 0)

    def recall_by_date(
        self,
        year: int,
        month: int,
        day: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        prefix = f"{year:04d}-{month:02d}"
        params: List[Any] = []
        query = f"SELECT * FROM raw_archive_entries WHERE archive_month = ? AND {self._active_record_clause()}"
        params.append(prefix)
        if day is not None:
            day_prefix = f"{year:04d}-{month:02d}-{day:02d}"
            query = f"SELECT * FROM raw_archive_entries WHERE created_at LIKE ? AND {self._active_record_clause()}"
            params = [f"{day_prefix}%"]
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def recall_by_time_phrase(
        self,
        phrase: str,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        target_now = now or datetime.now()
        normalized = (phrase or "").strip()
        if not normalized:
            return []

        if normalized == "去年":
            start = datetime(target_now.year - 1, 1, 1)
            end = datetime(target_now.year - 1, 12, 31, 23, 59, 59)
        elif normalized == "上个月":
            year = target_now.year
            month = target_now.month - 1
            if month == 0:
                year -= 1
                month = 12
            start = datetime(year, month, 1)
            if month == 12:
                end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
            else:
                end = datetime(year, month + 1, 1) - timedelta(seconds=1)
        else:
            year = target_now.year
            month = None
            day = None
            import re

            relative_year_month = re.search(
                r"去年(\d{1,2})月(?:(\d{1,2})[日号])?", normalized
            )
            if relative_year_month:
                year = target_now.year - 1
                month = int(relative_year_month.group(1))
                if relative_year_month.group(2):
                    day = int(relative_year_month.group(2))
                if day is not None:
                    return self.recall_by_date(year, month, day=day, limit=limit)
                return self.recall_by_date(year, month, limit=limit)

            match = re.search(
                r"(?:(\d{4})年)?(\d{1,2})月(?:(\d{1,2})[日号])?", normalized
            )
            if not match:
                return []
            if match.group(1):
                year = int(match.group(1))
            month = int(match.group(2))
            if match.group(3):
                day = int(match.group(3))
            if day is not None:
                return self.recall_by_date(year, month, day=day, limit=limit)
            return self.recall_by_date(year, month, limit=limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM raw_archive_entries WHERE created_at BETWEEN ? AND ? AND {self._active_record_clause()} ORDER BY created_at ASC LIMIT ?",
                (start.isoformat(), end.isoformat(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recall_by_time_and_query(
        self,
        phrase: str,
        query: str,
        *,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        scoped = self.recall_by_time_phrase(phrase, now=now, limit=500)
        if not query.strip():
            return scoped[:limit]

        keywords = [token.strip().lower() for token in query.split() if token.strip()]
        if not keywords:
            return scoped[:limit]

        ranked: List[tuple[int, Dict[str, Any]]] = []
        for row in scoped:
            searchable = " ".join(
                str(row.get(key, "") or "")
                for key in (
                    "raw_event",
                    "raw_thought",
                    "raw_lesson",
                    "raw_payload",
                    "full_text",
                )
            ).lower()
            score = sum(1 for token in keywords if token in searchable)
            if score <= 0:
                continue
            ranked.append((score, row))

        ranked.sort(key=lambda item: (-item[0], item[1].get("created_at", "")))
        return [row for _, row in ranked[:limit]]

    def recall_by_memory_ids(
        self, memory_ids: List[int], limit: int = 100
    ) -> List[Dict[str, Any]]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        query = (
            f"SELECT * FROM raw_archive_entries WHERE memory_entry_id IN ({placeholders}) AND {self._active_record_clause()} "
            "ORDER BY created_at ASC LIMIT ?"
        )
        params = [*memory_ids, limit]
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def search_by_keywords(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        keywords = [token.strip().lower() for token in query.split() if token.strip()]
        if not keywords:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM raw_archive_entries WHERE {self._active_record_clause()} ORDER BY created_at ASC"
            ).fetchall()

        ranked: List[tuple[int, Dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            searchable = " ".join(
                str(item.get(key, "") or "")
                for key in (
                    "raw_event",
                    "raw_thought",
                    "raw_lesson",
                    "raw_payload",
                    "full_text",
                )
            ).lower()
            score = sum(1 for token in keywords if token in searchable)
            if score <= 0:
                continue
            ranked.append((score, item))

        ranked.sort(key=lambda entry: (-entry[0], entry[1].get("created_at", "")))
        return [item for _, item in ranked[:limit]]

    def search_recent_solutions(
        self,
        memory_ids: List[int],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        rows = self.recall_by_memory_ids(memory_ids, limit=200)
        solutions: List[Dict[str, Any]] = []
        for row in sorted(
            rows, key=lambda item: item.get("created_at", ""), reverse=True
        ):
            searchable = " ".join(
                str(row.get(key, "") or "")
                for key in ("raw_event", "raw_thought", "raw_lesson", "raw_payload")
            )
            lowered = searchable.lower()
            if not (
                ("outcome: success" in lowered)
                or ("success" in lowered)
                or ("成功" in lowered)
            ):
                continue
            if not any(
                token in lowered
                for token in (
                    "解决",
                    "优化",
                    "修复",
                    "清理缓存",
                    "降低并发",
                    "release cache",
                    "reduce concurrency",
                )
            ):
                continue
            solutions.append(row)
            if len(solutions) >= limit:
                break
        return solutions

    def rebuild_monthly_summary(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT archive_month,
                       MIN(archive_seq) AS start_seq,
                       MAX(archive_seq) AS end_seq,
                       COUNT(*) AS record_count,
                       MIN(created_at) AS first_created_at,
                       MAX(created_at) AS last_created_at
                FROM raw_archive_entries
                GROUP BY archive_month
                ORDER BY archive_month ASC
                """
            ).fetchall()

            conn.execute("DELETE FROM raw_archive_monthly_summary")
            for row in rows:
                breakdown_rows = conn.execute(
                    "SELECT source_type, COUNT(*) AS count FROM raw_archive_entries WHERE archive_month = ? GROUP BY source_type",
                    (row["archive_month"],),
                ).fetchall()
                breakdown = {
                    item["source_type"] or "unknown": int(item["count"])
                    for item in breakdown_rows
                }
                conn.execute(
                    """
                    INSERT INTO raw_archive_monthly_summary (
                        archive_month, start_seq, end_seq, record_count,
                        first_created_at, last_created_at, source_type_breakdown, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["archive_month"],
                        int(row["start_seq"]),
                        int(row["end_seq"]),
                        int(row["record_count"]),
                        row["first_created_at"],
                        row["last_created_at"],
                        json.dumps(breakdown, ensure_ascii=False),
                        datetime.now().isoformat(),
                    ),
                )
        return {"months": len(rows)}

    def list_monthly_summaries(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_archive_monthly_summary ORDER BY archive_month ASC"
            ).fetchall()
        summaries: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["source_type_breakdown"] = json.loads(
                item["source_type_breakdown"] or "{}"
            )
            summaries.append(item)
        return summaries
