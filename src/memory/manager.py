"""记忆管理器模块"""

import json
from pathlib import Path
import re
from typing import Callable, List, Optional, Dict, Any
from datetime import datetime
from .storage import DatabaseManager, MemoryCompressor
from .retrieval import VectorRetriever, HybridRetriever
from .models import MemoryEntry, build_source_hash
from .distiller import MemoryDistiller
from .raw_archive import RawArchiveManager
from config.constants import (
    MEMORY_IMPORTANCE_THRESHOLD,
    MEMORY_DECAY_RATE,
    EPISODIC_MEMORY_MAX,
    SEMANTIC_MEMORY_MAX,
)
from config.settings import settings
from src.utils.logger import log
from src.utils.helpers import calculate_similarity


class MemoryManager:
    """记忆管理器"""

    def __init__(
        self, database_url: Optional[str] = None, chroma_db_path: Optional[str] = None
    ):
        """
        初始化记忆管理器

        Args:
            database_url: 数据库URL
            chroma_db_path: ChromaDB路径
        """
        self.db_manager = DatabaseManager(database_url)
        self.vector_retriever = VectorRetriever(chroma_db_path)
        self.hybrid_retriever = HybridRetriever(self.vector_retriever, self.db_manager)
        self.distiller = MemoryDistiller()
        self.raw_archive = RawArchiveManager()

        log.info("🧠 记忆管理器已初始化")

    def export_memory_governance_snapshot(self) -> Dict[str, Any]:
        memories = self.db_manager.list_memories(limit=100000)
        wisdom_entries = self.db_manager.list_semantic_wisdom(limit=100000)
        source_type_counter: Dict[str, int] = {}
        verification_counter: Dict[str, int] = {}
        stale_memory_ids: List[int] = []
        conflict_memory_ids: List[int] = []
        now = datetime.now()
        archive_rows = self.raw_archive.recall_by_memory_ids(
            [int(memory.id) for memory in memories], limit=100000
        )
        archive_by_memory_id = {
            int(row["memory_entry_id"]): row
            for row in archive_rows
            if row.get("memory_entry_id") is not None
        }

        for memory in memories:
            archive_row = archive_by_memory_id.get(int(memory.id), {})
            source_type = str(archive_row.get("source_type", "episodic") or "episodic")
            verification = str(
                archive_row.get("verification_status", "unknown") or "unknown"
            )
            source_type_counter[source_type] = (
                source_type_counter.get(source_type, 0) + 1
            )
            verification_counter[verification] = (
                verification_counter.get(verification, 0) + 1
            )
            age_hours = max(0.0, (now - memory.timestamp).total_seconds() / 3600.0)
            if age_hours >= 24 * 30:
                stale_memory_ids.append(int(memory.id))
            if verification == "conflicted":
                conflict_memory_ids.append(int(memory.id))

        source_chain_samples = []
        for wisdom in wisdom_entries[:10]:
            source_chain_samples.append(
                {
                    "wisdom_id": int(wisdom.id),
                    "category": wisdom.category,
                    "source_memory_ids": list(wisdom.source_memory_ids or []),
                    "source_sync_transaction_ids": list(
                        wisdom.source_sync_transaction_ids or []
                    ),
                }
            )

        return {
            "generated_at": now.isoformat(),
            "memory_count": len(memories),
            "wisdom_count": len(wisdom_entries),
            "source_type_counter": source_type_counter,
            "verification_counter": verification_counter,
            "stale_memory_count": len(stale_memory_ids),
            "stale_memory_ids": stale_memory_ids[:20],
            "conflict_memory_count": len(conflict_memory_ids),
            "conflict_memory_ids": conflict_memory_ids[:20],
            "source_chain_samples": source_chain_samples,
        }

    def reindex_local_embeddings(self) -> Dict[str, int]:
        """全量重建本地向量索引与 embedding。"""
        episodic_memories = self.db_manager.list_memories(limit=100000)
        wisdom_entries = self.db_manager.list_semantic_wisdom(limit=100000)

        self.vector_retriever.clear_all()

        episodic_reindexed = 0
        for memory in episodic_memories:
            memory_text = self._combine_memory_text(
                memory.event,
                memory.thought,
                memory.lesson,
            )
            embedding = self.vector_retriever.generate_embedding(memory_text)
            self.db_manager.update_memory(memory.id, embedding=embedding)
            self.vector_retriever.add_memory(
                memory_id=str(memory.id),
                text=memory_text,
                metadata={
                    "importance": memory.importance,
                    "timestamp": memory.timestamp.isoformat(),
                    "type": "episodic",
                    "sync_transaction_id": memory.sync_transaction_id,
                    "is_deleted": False,
                },
            )
            episodic_reindexed += 1

        wisdom_reindexed = 0
        for wisdom in wisdom_entries:
            embedding = self.vector_retriever.generate_embedding(wisdom.wisdom_text)
            self.db_manager.update_semantic_wisdom(wisdom.id, embedding=embedding)
            self.vector_retriever.add_memory(
                memory_id=f"wisdom:{wisdom.id}",
                text=wisdom.wisdom_text,
                metadata={
                    "importance": wisdom.importance,
                    "timestamp": wisdom.created_at.isoformat(),
                    "type": "semantic_wisdom",
                    "sync_transaction_id": wisdom.sync_transaction_id,
                    "source_memory_ids": wisdom.source_memory_ids,
                    "category": wisdom.category,
                    "x": wisdom.x,
                    "y": wisdom.y,
                    "z": wisdom.z,
                    "gravity": wisdom.gravity,
                    "is_deleted": False,
                },
            )
            wisdom_reindexed += 1

        log.info(
            "♻️ 本地向量重索引完成 | episodic={} | wisdom={}",
            episodic_reindexed,
            wisdom_reindexed,
        )
        return {
            "episodic_reindexed": episodic_reindexed,
            "wisdom_reindexed": wisdom_reindexed,
        }

    def create_memory(
        self,
        event: str,
        thought: Optional[str] = None,
        emotion: Optional[Dict[str, float]] = None,
        lesson: Optional[str] = None,
        importance: Optional[float] = None,
        source_type: str = "unknown",
        source_url: Optional[str] = None,
        source_reputation: Optional[float] = None,
        verification_status: str = "auto",
        raw_payload: Optional[Dict[str, Any]] = None,
        full_text: Optional[str] = None,
        raw_source_data: Optional[bytes | str] = None,
        source_encoding: Optional[str] = None,
        source_content_type: Optional[str] = None,
        permanence: bool = True,
    ) -> Optional[str]:
        """
        创建新记忆

        流程：
        1. 计算记忆的重要性
        2. 保存到数据库
        3. 添加到向量库
        4. 返回记忆ID

        Args:
            event: 事件描述
            thought: 想法
            emotion: 情绪状态
            lesson: 教训

        Returns:
            记忆ID，失败返回None
        """
        # 1. 计算重要性
        if importance is None:
            importance = self._calculate_importance(event, thought, emotion, lesson)

        memory_text = self._combine_memory_text(event, thought, lesson)
        duplicate_memory_id = self._should_merge_resource_warning(memory_text)
        if duplicate_memory_id is not None:
            log.info(
                "♻️ 资源预警高相似记忆已合并 | existing_id={} | similarity>0.95",
                duplicate_memory_id,
            )
            return str(duplicate_memory_id)

        archive_entry = self.raw_archive.create_entry(
            event=event,
            thought=thought,
            lesson=lesson,
            emotion=emotion,
            source_type=source_type,
            source_url=source_url,
            source_reputation=source_reputation,
            verification_status=verification_status,
            raw_payload=raw_payload,
            full_text=full_text,
            raw_source_data=raw_source_data,
            source_encoding=source_encoding,
            source_content_type=source_content_type,
            permanence=permanence,
        )

        # 2. 保存到数据库
        memory = self.db_manager.create_memory(
            event=event,
            thought=thought,
            emotion=emotion,
            lesson=lesson,
            importance=importance,
        )

        if not memory:
            return None

        sync_txn_id = memory.sync_transaction_id

        # 3. 添加到向量库
        success = self.vector_retriever.add_memory(
            memory_id=str(memory.id),
            text=memory_text,
            metadata={
                "importance": importance,
                "timestamp": memory.timestamp.isoformat(),
                "type": "episodic",
                "sync_transaction_id": sync_txn_id,
                "is_deleted": False,
            },
        )

        if success:
            self.raw_archive.bind_memory_entry(int(archive_entry["id"]), int(memory.id))
            log.info(f"💾 保存记忆: id={memory.id}, importance={importance:.2f}")
            return str(memory.id)
        else:
            self.db_manager.delete_memory(memory.id)
            log.error(f"❌ 保存记忆到向量库失败: id={memory.id}")
            return None

    def _should_merge_resource_warning(self, memory_text: str) -> Optional[int]:
        if not self._is_resource_warning(memory_text):
            return None
        similar_results = self.vector_retriever.search_similar(
            memory_text,
            top_k=3,
            min_similarity=0.95,
        )
        for result in similar_results:
            metadata = result.get("metadata", {}) or {}
            if metadata.get("type") != "episodic":
                continue
            existing_id = result.get("id")
            if existing_id is None:
                continue
            return int(existing_id)
        return None

    @staticmethod
    def _is_resource_warning(text: str) -> bool:
        lowered = (text or "").lower()
        markers = (
            "资源",
            "显存",
            "gpu",
            "cpu",
            "内存",
            "水位",
            "过载",
            "告警",
            "pressure",
            "memory pressure",
            "resource pressure",
            "host_resource_pressure",
        )
        return any(marker in lowered for marker in markers)

    def retrieve_memories(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        检索相关记忆

        使用混合检索策略

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            记忆列表
        """
        results = self.hybrid_retriever.search(query, top_k=top_k)

        # 获取完整记忆信息
        memories = []
        for result in results:
            memory = self.db_manager.get_memory_by_id(int(result["id"]))
            if memory:
                memories.append(
                    {
                        "id": result["id"],
                        "event": memory.event,
                        "thought": memory.thought,
                        "emotion": memory.emotion,
                        "lesson": memory.lesson,
                        "importance": memory.importance,
                        "timestamp": memory.timestamp.isoformat(),
                        "score": result["combined_score"],
                        "sync_transaction_id": memory.sync_transaction_id,
                    }
                )

        return memories

    def recall_archive_by_date(
        self,
        year: int,
        month: int,
        day: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.raw_archive.recall_by_date(year, month, day=day, limit=limit)

    def recall_archive_by_time_phrase(
        self,
        phrase: str,
        *,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.raw_archive.recall_by_time_phrase(phrase, now=now, limit=limit)

    def recall_archive_by_time_and_query(
        self,
        phrase: str,
        query: str,
        *,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.raw_archive.recall_by_time_and_query(
            phrase,
            query,
            now=now,
            limit=limit,
        )

    def recall_archive_by_anchor(
        self, anchor: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        wisdom_entries = self.db_manager.search_semantic_wisdom(anchor, limit=20)
        matching_memory_ids = []
        for wisdom in wisdom_entries:
            if anchor not in wisdom.wisdom_text and anchor not in (
                wisdom.category or ""
            ):
                pass
            matching_memory_ids.extend(
                int(item) for item in (wisdom.source_memory_ids or [])
            )
        deduped_ids = sorted({memory_id for memory_id in matching_memory_ids})
        return self.raw_archive.recall_by_memory_ids(deduped_ids, limit=limit)

    def recall_context(
        self,
        *,
        time_phrase: Optional[str] = None,
        anchor: Optional[str] = None,
        query: Optional[str] = None,
        now: Optional[datetime] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        candidates: Dict[str, List[Dict[str, Any]]] = {}
        if time_phrase:
            candidates["time"] = self.recall_archive_by_time_phrase(
                time_phrase,
                now=now,
                limit=500,
            )
        if anchor:
            candidates["anchor"] = self.recall_archive_by_anchor(anchor, limit=500)
        if query:
            candidates["query"] = self._recall_archive_by_query(query, limit=500)

        if not candidates:
            return {
                "filters": {
                    "time_phrase": time_phrase,
                    "anchor": anchor,
                    "query": query,
                },
                "matched_entries": [],
                "matched_archive_ids": [],
                "matched_memory_ids": [],
                "matched_months": [],
            }

        scored: Dict[int, Dict[str, Any]] = {}
        for key, rows in candidates.items():
            for rank, row in enumerate(rows):
                archive_id = int(row["id"])
                item = scored.setdefault(
                    archive_id,
                    {
                        "row": row,
                        "score": 0,
                        "matched_filters": set(),
                        "best_rank": rank,
                    },
                )
                item["score"] += 1
                item["matched_filters"].add(key)
                item["best_rank"] = min(int(item.get("best_rank", rank)), rank)

        required_filters = set(candidates.keys())
        filtered = []
        for item in scored.values():
            if required_filters.issubset(item["matched_filters"]):
                filtered.append(item)

        filtered.sort(
            key=lambda item: (
                -item["score"],
                item.get("best_rank", 0),
                item["row"].get("created_at", ""),
                item["row"].get("id", 0),
            )
        )
        matched_entries = [item["row"] for item in filtered[:limit]]
        return {
            "filters": {"time_phrase": time_phrase, "anchor": anchor, "query": query},
            "matched_entries": matched_entries,
            "matched_archive_ids": [int(item["id"]) for item in matched_entries],
            "matched_memory_ids": [
                int(item["memory_entry_id"])
                for item in matched_entries
                if item.get("memory_entry_id") is not None
            ],
            "matched_months": sorted(
                {item["archive_month"] for item in matched_entries}
            ),
        }

    def recall_for_dialogue(
        self,
        user_text: str,
        *,
        time_phrase: Optional[str] = None,
        anchor: Optional[str] = None,
        query: Optional[str] = None,
        now: Optional[datetime] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        inferred_time = time_phrase or self._infer_time_phrase_from_text(user_text)
        inferred_anchor = anchor or self._infer_anchor_from_text(user_text)
        inferred_query = query or self._infer_query_terms(
            user_text, inferred_time, inferred_anchor
        )
        dialogue_terms = self._extract_dialogue_terms(
            user_text=user_text,
            query=inferred_query,
            anchor=inferred_anchor,
        )

        recalled = self.recall_context(
            time_phrase=inferred_time,
            anchor=inferred_anchor,
            query=inferred_query,
            now=now,
            limit=limit,
        )
        if not recalled.get("matched_entries") and inferred_query:
            # Dialogue queries often contain noisy trailing words; fall back to time+anchor first.
            recalled = self.recall_context(
                time_phrase=inferred_time,
                anchor=inferred_anchor,
                query=None,
                now=now,
                limit=limit,
            )
        recalled = self._enrich_dialogue_recall(recalled, dialogue_terms)
        return {
            "user_text": user_text,
            "filters": {
                "time_phrase": inferred_time,
                "anchor": inferred_anchor,
                "query": inferred_query,
            },
            "dialogue_terms": dialogue_terms,
            "recalled": recalled,
            "answer_hint": self._build_dialogue_answer_hint(recalled),
        }

    def _recall_archive_by_query(
        self, query: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        keyword_recalled = self.raw_archive.search_by_keywords(query, limit=limit)
        similar_results = self.vector_retriever.search_similar(
            query,
            top_k=50,
            min_similarity=0.35,
        )
        matching_memory_ids = []
        for result in similar_results:
            metadata = result.get("metadata", {}) or {}
            if metadata.get("type") != "episodic":
                continue
            memory_id = result.get("id")
            if memory_id is None:
                continue
            matching_memory_ids.append(int(memory_id))
        deduped_ids = sorted({memory_id for memory_id in matching_memory_ids})
        vector_recalled = self.raw_archive.recall_by_memory_ids(
            deduped_ids, limit=limit
        )

        merged: List[Dict[str, Any]] = []
        seen_archive_ids = set()
        for rows in (keyword_recalled, vector_recalled):
            for row in rows:
                archive_id = row.get("id")
                if archive_id in seen_archive_ids:
                    continue
                seen_archive_ids.add(archive_id)
                merged.append(row)
                if len(merged) >= limit:
                    return merged
        return merged

    def _infer_time_phrase_from_text(self, text: str) -> Optional[str]:
        normalized = (text or "").strip()
        if not normalized:
            return None
        for marker in ("去年", "上个月"):
            if marker in normalized:
                scoped = re.search(
                    rf"{marker}(\d{{1,2}}月(?:\d{{1,2}}[日号])?)", normalized
                )
                if scoped:
                    return f"{marker}{scoped.group(1)}"
                return marker
        absolute = re.search(r"(?:\d{4}年)?\d{1,2}月(?:\d{1,2}[日号])?", normalized)
        if absolute:
            return absolute.group(0)
        return None

    def _infer_anchor_from_text(self, text: str) -> Optional[str]:
        normalized = (text or "").strip()
        if not normalized:
            return None
        for anchor in sorted(
            self._load_wisdom_anchor_candidates(), key=len, reverse=True
        ):
            if anchor and anchor in normalized:
                return anchor
        return None

    def _infer_query_terms(
        self,
        user_text: str,
        time_phrase: Optional[str],
        anchor: Optional[str],
    ) -> str:
        normalized = (user_text or "").strip()
        if time_phrase:
            normalized = normalized.replace(time_phrase, " ")
        if anchor:
            normalized = normalized.replace(anchor, " ")
        normalized = re.sub(
            r"(我|去年|上个月|什么时候|那个事|那个|来着|如何|怎么样了|背后|原文|对应|记录)",
            " ",
            normalized,
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _build_dialogue_answer_hint(self, recalled: Dict[str, Any]) -> str:
        entries = recalled.get("matched_entries", [])
        if not entries:
            return "当前没有找到匹配的原始记忆。"
        first = entries[0]
        created_at = str(first.get("created_at", ""))
        event = str(first.get("raw_event", "") or "")
        month = first.get("archive_month")
        excerpt = str(first.get("dialogue_excerpt", "") or "").strip()
        searchable = " ".join(
            str(first.get(key, "") or "")
            for key in ("raw_event", "raw_thought", "raw_lesson", "raw_payload")
        ).lower()
        success_prefix = (
            "已命中成功经验。"
            if any(
                token in searchable for token in ("outcome: success", "success", "成功")
            )
            else ""
        )
        if excerpt:
            return (
                f"{success_prefix}已在 {month} 的原始冷库中找到记录，最早命中时间是 {created_at}，"
                f"对应事件是：{event}。原文细节：{excerpt}"
            )
        return (
            f"{success_prefix}已在 {month} 的原始冷库中找到记录，最早命中时间是 {created_at}，"
            f"对应事件是：{event}"
        )

    def _extract_dialogue_terms(
        self, *, user_text: str, query: Optional[str], anchor: Optional[str]
    ) -> List[str]:
        candidate_text = " ".join(
            filter(None, [anchor or "", query or "", user_text or ""])
        )
        parts = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", candidate_text)
        stop_words = {
            "去年",
            "上个月",
            "原文",
            "记录",
            "对应",
            "来着",
            "那个",
            "问题",
            "怎么",
            "什么",
            "时候",
            "细节",
        }
        terms: List[str] = []
        for part in parts:
            normalized = part.strip()
            if len(normalized) < 2 or normalized in stop_words:
                continue
            if normalized not in terms:
                terms.append(normalized)
        return terms[:8]

    def _enrich_dialogue_recall(
        self, recalled: Dict[str, Any], dialogue_terms: List[str]
    ) -> Dict[str, Any]:
        entries = recalled.get("matched_entries", []) or []
        enriched_entries: List[Dict[str, Any]] = []
        for entry in entries:
            item = dict(entry)
            excerpt = self._extract_dialogue_excerpt(item, dialogue_terms)
            if excerpt:
                item["dialogue_excerpt"] = excerpt
            enriched_entries.append(item)
        recalled["matched_entries"] = enriched_entries
        return recalled

    def _extract_dialogue_excerpt(
        self, entry: Dict[str, Any], dialogue_terms: List[str]
    ) -> str:
        full_text = str(entry.get("full_text", "") or "").strip()
        source_text = full_text or " ".join(
            filter(
                None,
                [
                    str(entry.get("raw_event", "") or ""),
                    str(entry.get("raw_thought", "") or ""),
                    str(entry.get("raw_lesson", "") or ""),
                ],
            )
        )
        if not source_text:
            return ""

        chunks = [
            segment.strip()
            for segment in re.split(r"(?<=[。！？!?])|\n+", source_text)
            if segment.strip()
        ]
        if not chunks:
            chunks = [source_text]

        lowered_terms = [term.lower() for term in dialogue_terms if term]
        best_chunk = chunks[0]
        best_score = -1
        for chunk in chunks:
            lowered = chunk.lower()
            score = sum(1 for term in lowered_terms if term in lowered)
            if score > best_score or (
                score == best_score and len(chunk) < len(best_chunk)
            ):
                best_chunk = chunk
                best_score = score

        excerpt = best_chunk.strip()
        if len(excerpt) > 180:
            excerpt = excerpt[:180].rstrip() + "..."
        return excerpt

    def _load_wisdom_anchor_candidates(self) -> List[str]:
        map_path = Path(settings.BASE_DIR) / "evolution_map.json"
        if not map_path.exists():
            return []
        try:
            payload = json.loads(map_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        anchors = [
            str(node.get("anchor", "")).strip()
            for node in payload.get("wisdom_nodes", [])
            if str(node.get("anchor", "")).strip()
        ]
        return sorted(set(anchors))

    def backtrack_recent_solution(
        self,
        action_query: str,
        anchor_hint: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        memory_results = self.vector_retriever.search_similar(
            action_query,
            top_k=10,
            min_similarity=0.55,
        )
        matching_memory_ids: List[int] = []
        for result in memory_results:
            metadata = result.get("metadata", {}) or {}
            if metadata.get("type") != "episodic":
                continue
            memory_id = result.get("id")
            if memory_id is None:
                continue
            matching_memory_ids.append(int(memory_id))

        matched_anchor = anchor_hint
        if anchor_hint:
            matching_memory_ids.extend(
                int(item["memory_entry_id"])
                for item in self.recall_archive_by_anchor(anchor_hint, limit=50)
                if item.get("memory_entry_id") is not None
            )

        deduped_ids = sorted({memory_id for memory_id in matching_memory_ids})
        if not deduped_ids:
            return {
                "matched_anchor": matched_anchor,
                "solution": None,
                "memory_ids": (),
            }

        solutions = self.raw_archive.search_recent_solutions(deduped_ids, limit=limit)
        if not solutions:
            return {
                "matched_anchor": matched_anchor,
                "solution": None,
                "memory_ids": tuple(deduped_ids),
            }

        best = solutions[0]
        solution = str(
            best.get("raw_lesson")
            or best.get("raw_thought")
            or best.get("raw_event")
            or ""
        ).strip()
        return {
            "matched_anchor": matched_anchor,
            "solution": solution,
            "memory_ids": tuple(deduped_ids[:limit]),
        }

    def rebuild_raw_archive_monthly_summary(self) -> Dict[str, int]:
        return self.raw_archive.rebuild_monthly_summary()

    def list_raw_archive_monthly_summaries(self) -> List[Dict[str, Any]]:
        return self.raw_archive.list_monthly_summaries()

    def mark_deprecated_empty_web_archive_records(self) -> int:
        return self.raw_archive.mark_deprecated_empty_web_records()

    def get_raw_archive_storage_health(self) -> Dict[str, Any]:
        return self.raw_archive.get_storage_health()

    def rollback_pollution(
        self, memory_id: int, map_path: Optional[str] = None
    ) -> Dict[str, Any]:
        vector_deleted = self.vector_retriever.delete_memory(str(memory_id))
        archive_deleted = self.raw_archive.delete_by_memory_entry_id(memory_id)
        memory_deleted = self.db_manager.delete_memory(memory_id)
        wisdom_ids = self.db_manager.soft_delete_semantic_wisdom_by_source_memory_id(
            memory_id
        )
        wisdom_vectors_deleted = 0
        for wisdom_id in wisdom_ids:
            if self.vector_retriever.delete_memory(f"wisdom:{wisdom_id}"):
                wisdom_vectors_deleted += 1
        map_nodes_marked = self._mark_wisdom_nodes_re_evaluating(
            wisdom_ids, map_path=map_path
        )
        return {
            "memory_id": memory_id,
            "memory_deleted": memory_deleted,
            "vector_deleted": vector_deleted,
            "archive_deleted": archive_deleted,
            "wisdom_ids": wisdom_ids,
            "wisdom_vectors_deleted": wisdom_vectors_deleted,
            "map_nodes_marked": map_nodes_marked,
        }

    def _mark_wisdom_nodes_re_evaluating(
        self, wisdom_ids: List[int], map_path: Optional[str] = None
    ) -> int:
        if not wisdom_ids:
            return 0
        queue_path = (
            Path(settings.BASE_DIR)
            / "data"
            / "reports"
            / "wisdom_re_evaluation_queue.json"
        )
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = (
                json.loads(queue_path.read_text(encoding="utf-8"))
                if queue_path.exists()
                else {}
            )
        except Exception:
            existing = {}
        queued = {
            int(item) for item in existing.get("wisdom_ids", []) if str(item).isdigit()
        }
        queued.update(int(item) for item in wisdom_ids)
        payload = {
            "generated_at": datetime.now().isoformat(),
            "map_path": str(map_path or Path(settings.BASE_DIR) / "evolution_map.json"),
            "wisdom_ids": sorted(queued),
            "status": "re-evaluating",
        }
        queue_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return len(wisdom_ids)

    def update_memory_importance(self, memory_id: int, new_importance: float) -> bool:
        """
        更新记忆重要性

        Args:
            memory_id: 记忆ID
            new_importance: 新的重要性

        Returns:
            是否更新成功
        """
        return self.db_manager.update_memory(memory_id, importance=new_importance)

    def decay_old_memories(self) -> int:
        """
        衰减旧记忆的重要性

        定期调用，降低旧记忆的重要性

        Returns:
            被衰减的记忆数量
        """
        from datetime import timedelta

        count = 0
        current_time = datetime.now()
        memories = self.db_manager.get_recent_memories(
            hours=24 * 30, limit=1000
        )  # 最近30天

        for memory in memories:
            hours_since_creation = (
                current_time - memory.timestamp
            ).total_seconds() / 3600
            decayed_importance = MemoryCompressor.calculate_importance_decay(
                memory.importance, hours_since_creation, MEMORY_DECAY_RATE
            )

            # 只在重要性变化较大时更新
            if abs(decayed_importance - memory.importance) > 0.01:
                self.db_manager.update_memory(memory.id, importance=decayed_importance)
                count += 1

        if count > 0:
            log.debug(f"📉 衰减了 {count} 条旧记忆的重要性")

        return count

    def compress_memories(self) -> Dict[str, int]:
        """
        压缩记忆

        策略：
        1. 删除低重要性、长时间未访问的记忆
        2. 合并相似的短期记忆
        3. 提取重要记忆的摘要

        Returns:
            压缩统计信息
        """
        current_time = datetime.now()
        all_memories = self.db_manager.get_recent_memories(
            hours=24 * 365, limit=10000
        )  # 最近1年

        deleted_count = 0
        compressed_count = 0

        for memory in all_memories:
            if MemoryCompressor.should_compress(memory, current_time):
                # 删除记忆
                self.db_manager.delete_memory(memory.id)
                self.vector_retriever.delete_memory(str(memory.id))
                deleted_count += 1

        # 检查记忆总数，如果超过限制，删除最不重要的
        total_count = self.db_manager.count_memories()
        if total_count > EPISODIC_MEMORY_MAX:
            excess = total_count - EPISODIC_MEMORY_MAX
            # 获取最不重要的记忆
            least_important = sorted(all_memories, key=lambda m: m.importance)[:excess]
            for memory in least_important:
                self.db_manager.delete_memory(memory.id)
                self.vector_retriever.delete_memory(str(memory.id))
                deleted_count += 1

        log.info(f"🗜️ 记忆压缩完成: 删除 {deleted_count} 条, 压缩 {compressed_count} 条")

        return {
            "deleted": deleted_count,
            "compressed": compressed_count,
            "total_remaining": self.db_manager.count_memories(),
        }

    def get_memory_statistics(self) -> Dict[str, Any]:
        """
        获取记忆统计信息

        Returns:
            统计信息
        """
        stats = self.db_manager.get_statistics()
        stats["vector_db_count"] = self.vector_retriever.get_memory_count()
        return stats

    def maybe_distill_memory(
        self,
        episodic_count: Optional[int] = None,
        goal_completed: bool = False,
        generator: Optional[Callable[[str], str]] = None,
        trigger_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        episodic_total = (
            episodic_count
            if episodic_count is not None
            else self.db_manager.count_memories(memory_type="episodic")
        )
        if not self.distiller.should_distill(
            episodic_total, goal_completed=goal_completed
        ):
            return {
                "triggered": False,
                "created": 0,
                "skipped": 0,
                "wisdom_ids": [],
                "source_memory_ids": [],
                "trigger_type": trigger_type
                or ("goal_completed" if goal_completed else "capacity"),
            }

        return self.distill_memory(
            trigger_type=trigger_type
            or ("goal_completed" if goal_completed else "capacity"),
            generator=generator,
        )

    def distill_memory(
        self,
        source_memories: Optional[List[MemoryEntry]] = None,
        trigger_type: str = "capacity",
        generator: Optional[Callable[[str], str]] = None,
        distillation_directives: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        source_memories = source_memories or self.distiller.select_candidates(
            self.db_manager.get_recent_memories(
                hours=24 * 365, limit=64, memory_type="episodic"
            )
        )
        if len(source_memories) < self.distiller.min_candidate_count:
            return {
                "triggered": True,
                "created": 0,
                "skipped": 0,
                "wisdom_ids": [],
                "source_memory_ids": [memory.id for memory in source_memories],
                "trigger_type": trigger_type,
            }

        source_memory_ids = [memory.id for memory in source_memories]
        source_hash = build_source_hash(source_memory_ids)
        existing = self.db_manager.find_wisdom_by_source_hash(source_hash)
        if existing:
            return {
                "triggered": True,
                "created": 0,
                "skipped": 1,
                "wisdom_ids": [existing.id],
                "source_memory_ids": source_memory_ids,
                "trigger_type": trigger_type,
                "category": existing.category,
                "x": existing.x,
                "y": existing.y,
                "z": existing.z,
                "gravity": existing.gravity,
            }

        context = self.distiller.build_distillation_context(
            source_memories, self._combine_memory_text
        )
        wisdom_text = self.distiller.generate_wisdom(
            context,
            generator=generator,
            trigger_type=trigger_type,
            directives=distillation_directives,
        )
        embedding = self.vector_retriever.generate_embedding(wisdom_text)
        force_new_node = self.vector_retriever.should_force_new_node(
            embedding, threshold=0.7
        )
        category = self.distiller.infer_category(wisdom_text, source_memories)
        x, y, z = self.distiller.calculate_spatial_coords(embedding, category)
        importance = max(memory.importance for memory in source_memories)
        gravity = self.distiller.calculate_gravity(
            importance=importance,
            source_count=len(source_memories),
            category=category,
        )
        wisdom = self.db_manager.create_semantic_wisdom(
            wisdom_text=wisdom_text,
            source_memory_ids=source_memory_ids,
            source_sync_transaction_ids=[
                memory.sync_transaction_id for memory in source_memories
            ],
            trigger_type=trigger_type,
            style="aphorism",
            importance=importance,
            category=category,
            x=x,
            y=y,
            z=z,
            gravity=gravity,
            embedding=embedding,
            force_new=force_new_node,
        )
        if not wisdom:
            return {
                "triggered": True,
                "created": 0,
                "skipped": 0,
                "wisdom_ids": [],
                "source_memory_ids": source_memory_ids,
                "trigger_type": trigger_type,
            }

        vector_metadata = {
            "importance": wisdom.importance,
            "timestamp": wisdom.created_at.isoformat(),
            "type": "semantic_wisdom",
            "sync_transaction_id": wisdom.sync_transaction_id,
            "source_memory_ids": wisdom.source_memory_ids,
            "category": wisdom.category,
            "x": wisdom.x,
            "y": wisdom.y,
            "z": wisdom.z,
            "gravity": wisdom.gravity,
            "is_deleted": False,
        }
        vector_memory_id = f"wisdom:{wisdom.id}"
        was_created = bool(getattr(wisdom, "_was_created", True))
        if was_created:
            self.vector_retriever.add_memory(
                memory_id=vector_memory_id,
                text=wisdom.wisdom_text,
                metadata=vector_metadata,
            )
        else:
            self.vector_retriever.update_memory(
                memory_id=vector_memory_id,
                text=wisdom.wisdom_text,
                metadata=vector_metadata,
            )
        return {
            "triggered": True,
            "created": 1 if was_created else 0,
            "skipped": 0 if was_created else 1,
            "wisdom_ids": [wisdom.id],
            "source_memory_ids": source_memory_ids,
            "trigger_type": trigger_type,
            "category": wisdom.category,
            "x": wisdom.x,
            "y": wisdom.y,
            "z": wisdom.z,
            "gravity": wisdom.gravity,
            "dedup_reason": getattr(wisdom, "_dedup_reason", None),
            "force_new_node": force_new_node,
        }

    def retrieve_semantic_wisdom(
        self, query: str, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        candidate_limit = max(top_k * 5, 10)
        vector_results = self.vector_retriever.search_similar(
            query, top_k=candidate_limit, min_similarity=0.0
        )
        query_embedding = self.vector_retriever.generate_embedding(query)
        query_category = self.distiller.infer_category(query)
        query_coords = self.distiller.calculate_spatial_coords(
            query_embedding, query_category
        )

        vector_candidates = {}
        for result in vector_results:
            memory_id = result["id"]
            if not str(memory_id).startswith("wisdom:"):
                continue
            wisdom_id = int(str(memory_id).split(":", 1)[1])
            current = vector_candidates.get(wisdom_id)
            if current is None or result["similarity"] > current:
                vector_candidates[wisdom_id] = result["similarity"]

        candidate_ids = list(vector_candidates.keys())
        keyword_candidates = self.db_manager.search_semantic_wisdom(
            query, limit=candidate_limit
        )
        for wisdom in keyword_candidates:
            candidate_ids.append(wisdom.id)
            vector_candidates.setdefault(wisdom.id, 0.0)

        wisdom_results = []
        seen_ids = set()
        for wisdom_id in candidate_ids:
            if wisdom_id in seen_ids:
                continue
            seen_ids.add(wisdom_id)
            wisdom = self.db_manager.get_semantic_wisdom_by_id(wisdom_id)
            if not wisdom:
                continue
            similarity = vector_candidates.get(wisdom_id, 0.0)
            distance_xyz = self.distiller.calculate_distance_xyz(
                query_coords, (wisdom.x, wisdom.y, wisdom.z)
            )
            delta_z = self.distiller.calculate_z_layer_delta(query_coords[2], wisdom.z)
            final_score = self.distiller.calculate_gravity_score(
                similarity=similarity,
                gravity=wisdom.gravity,
                distance_xyz=distance_xyz,
                delta_z=delta_z,
            )
            wisdom_results.append(
                {
                    "id": wisdom.id,
                    "wisdom_text": wisdom.wisdom_text,
                    "score": final_score,
                    "similarity": similarity,
                    "gravity": wisdom.gravity,
                    "distance_xyz": distance_xyz,
                    "delta_z": delta_z,
                    "category": wisdom.category,
                    "x": wisdom.x,
                    "y": wisdom.y,
                    "z": wisdom.z,
                    "source_memory_ids": wisdom.source_memory_ids,
                    "source_sync_transaction_ids": wisdom.source_sync_transaction_ids,
                    "trigger_type": wisdom.trigger_type,
                    "sync_transaction_id": wisdom.sync_transaction_id,
                }
            )

        if wisdom_results:
            wisdom_results.sort(key=lambda item: item["score"], reverse=True)
            return wisdom_results[:top_k]

        return []

    def _calculate_importance(
        self,
        event: str,
        thought: Optional[str],
        emotion: Optional[Dict[str, float]],
        lesson: Optional[str],
    ) -> float:
        """
        计算记忆的重要性

        影响因素：
        1. 情绪强度（情绪值的绝对值之和）
        2. 是否包含教训（有教训则更重要）
        3. 文本长度（过短或过长可能不重要）
        4. 关键词匹配（包含重要词汇）

        Args:
            event: 事件
            thought: 想法
            emotion: 情绪
            lesson: 教训

        Returns:
            重要性评分（0-1）
        """
        importance = 0.5  # 基础值

        # 1. 情绪强度
        if emotion:
            emotion_intensity = sum(abs(v) for v in emotion.values()) / len(emotion)
            importance += emotion_intensity * 0.3

        # 2. 是否有教训
        if lesson and len(lesson.strip()) > 10:
            importance += 0.2

        # 3. 文本长度
        text_length = len(event) + (len(thought) if thought else 0)
        if 50 < text_length < 1000:  # 适中的长度
            importance += 0.1

        # 4. 关键词（简单实现）
        important_keywords = ["错误", "失败", "成功", "学习", "经验", "重要", "关键"]
        text = (event + " " + (thought or "") + " " + (lesson or "")).lower()
        keyword_count = sum(1 for kw in important_keywords if kw in text)
        importance += keyword_count * 0.05

        # 限制在0-1范围内
        from src.utils.helpers import clamp

        return clamp(importance, 0.0, 1.0)

    def _combine_memory_text(
        self, event: str, thought: Optional[str], lesson: Optional[str]
    ) -> str:
        """
        组合记忆文本用于向量嵌入

        Args:
            event: 事件
            thought: 想法
            lesson: 教训

        Returns:
            组合后的文本
        """
        parts = [event]
        if thought:
            parts.append(f"想法: {thought}")
        if lesson:
            parts.append(f"教训: {lesson}")

        return " ".join(parts)

    def find_similar_memories(
        self, event: str, threshold: float = 0.8
    ) -> List[MemoryEntry]:
        """
        查找相似的记忆（用于去重）

        Args:
            event: 事件文本
            threshold: 相似度阈值

        Returns:
            相似记忆列表
        """
        similar_results = self.vector_retriever.search_similar(
            event, top_k=5, min_similarity=threshold
        )

        memories = []
        for result in similar_results:
            memory = self.db_manager.get_memory_by_id(int(result["id"]))
            if memory:
                memories.append(memory)

        return memories


class ShortTermMemory:
    """短期记忆（上下文窗口）"""

    def __init__(self, max_size: int = 10):
        """
        初始化短期记忆

        Args:
            max_size: 最大记忆条数
        """
        self.max_size = max_size
        self.memories: List[Dict[str, Any]] = []

    def add(self, content: str, role: str = "user"):
        """
        添加记忆

        Args:
            content: 内容
            role: 角色（user/assistant）
        """
        self.memories.append(
            {
                "content": content,
                "role": role,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # 限制大小
        if len(self.memories) > self.max_size:
            self.memories.pop(0)

    def get_context(self) -> str:
        """获取上下文字符串"""
        return "\n".join([f"{m['role']}: {m['content']}" for m in self.memories])

    def clear(self):
        """清空短期记忆"""
        self.memories.clear()

    def __len__(self):
        return len(self.memories)
