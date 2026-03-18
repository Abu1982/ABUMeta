"""记忆检索模块"""

from __future__ import annotations

from datetime import datetime
import importlib
import json
import math
import sqlite3
from typing import List, Optional, Dict, Any
import os
import re
from pathlib import Path

try:
    torch = importlib.import_module("torch")
except ModuleNotFoundError:  # pragma: no cover - 开源演示版允许无 torch 运行
    torch = None
from config.settings import settings
from config.constants import (
    MEMORY_RETRIEVAL_SIMILARITY_THRESHOLD,
    MEMORY_RETRIEVAL_THRESHOLD_DENSITY_FACTOR,
    MEMORY_RETRIEVAL_THRESHOLD_MIN,
    MEMORY_RETRIEVAL_THRESHOLD_MAX,
)
from src.utils.logger import log


FALLBACK_QUERY_SYNONYMS = {
    "ai": ("人工智能", "模型", "大模型"),
    "投资": ("投入", "资金", "绩效", "回报"),
    "机会": ("落地", "场景", "风口"),
    "显卡": ("显存", "gpu"),
    "缓存": ("清缓存", "释放缓存", "cache"),
    "研判": ("判断", "分析", "信源"),
    "日志": ("log", "请求", "回溯"),
    "优化": ("修复", "改进", "提速"),
    "成功": ("success", "成功", "恢复", "解决"),
}


class VectorRetriever:
    """向量检索器"""

    def __init__(
        self,
        chroma_db_path: Optional[str] = None,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ):
        """
        初始化向量检索器

        Args:
            chroma_db_path: ChromaDB数据库路径
            model_name: 嵌入模型名称
        """
        self.chroma_db_path = chroma_db_path or settings.CHROMA_DB_PATH
        self.model_name = model_name
        self.backend = "chroma"
        self.client = None
        self.collection = None
        self.embedding_model = None
        self.embedding_dimension = 384
        self._memory_store: Dict[str, Dict[str, Any]] = {}
        self._fallback_conn: Optional[sqlite3.Connection] = None
        self._fallback_db_path: Optional[Path] = None

        self._initialize_backend()

    def close(self) -> None:
        if self._fallback_conn is not None:
            self._fallback_conn.close()
            self._fallback_conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def should_force_new_node(
        self,
        embedding: List[float],
        threshold: float = 0.7,
    ) -> bool:
        """当新信息与现有知识节点相似度低于阈值时，强制裂变新节点。"""
        if self.backend in {"in_memory", "sqlite_fallback"}:
            best_similarity = 0.0
            items = (
                self._memory_store.values()
                if self.backend == "in_memory"
                else self._sqlite_fallback_items()
            )
            for item in items:
                metadata = item.get("metadata", {}) or {}
                if metadata.get("type") != "semantic_wisdom":
                    continue
                best_similarity = max(
                    best_similarity,
                    self._cosine_similarity(embedding, item.get("embedding", [])),
                )
            return best_similarity < threshold
        if self.collection is None:
            raise RuntimeError("Chroma collection 尚未初始化")
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=16,
            where={"type": "semantic_wisdom"},
            include=["distances", "metadatas"],
        )
        distances = results.get("distances") or []
        if not distances or not distances[0]:
            return True
        best_similarity = max(1 - float(distance) for distance in distances[0])
        return best_similarity < threshold

    def _initialize_backend(self) -> None:
        """强制初始化本地 ChromaDB + GPU Embedding 后端。"""
        try:
            if torch is None or not torch.cuda.is_available():
                raise RuntimeError(
                    "本地向量后端要求 torch + CUDA 可用，但当前环境不满足条件"
                )

            model_cache = Path(settings.BASE_DIR) / "data" / "models"
            model_cache.mkdir(parents=True, exist_ok=True)
            local_model_path = model_cache / self.model_name
            if not local_model_path.exists():
                raise RuntimeError(
                    f"本地语义模型未缓存到 {local_model_path}，请先下载模型后再启动本地向量后端"
                )

            chromadb_module = importlib.import_module("chromadb")
            chromadb_config = importlib.import_module("chromadb.config")
            sentence_transformers_module = importlib.import_module(
                "sentence_transformers"
            )

            PersistentClient = getattr(chromadb_module, "PersistentClient")
            ChromaSettings = getattr(chromadb_config, "Settings")
            SentenceTransformer = getattr(
                sentence_transformers_module, "SentenceTransformer"
            )

            client = PersistentClient(
                path=self.chroma_db_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )

            self.embedding_model = SentenceTransformer(
                str(local_model_path),
                device="cuda",
                cache_folder=str(model_cache),
                local_files_only=True,
            )
            sample_embedding = self.embedding_model.encode(
                "向量引擎预热",
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            self.embedding_dimension = int(
                getattr(sample_embedding, "shape", [len(sample_embedding)])[0]
            )
            self.client = client
            self.collection = collection
            self.backend = "chroma"
            log.info(
                "🔍 本地向量检索器已初始化 | backend=chroma | model={} | device=cuda | dimension={}",
                self.model_name,
                self.embedding_dimension,
            )
        except Exception as exc:
            self._initialize_sqlite_fallback(exc)

    def _initialize_sqlite_fallback(self, reason: Exception) -> None:
        self.backend = "sqlite_fallback"
        self.client = None
        self.collection = None
        self.embedding_model = None
        self.embedding_dimension = 384
        fallback_root = Path(self.chroma_db_path)
        if fallback_root.suffix:
            fallback_root = fallback_root.parent
        fallback_root.mkdir(parents=True, exist_ok=True)
        self._fallback_db_path = fallback_root / "fallback_vectors.sqlite3"
        conn = sqlite3.connect(self._fallback_db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fallback_vectors (
                memory_id TEXT PRIMARY KEY,
                document TEXT NOT NULL,
                metadata TEXT,
                embedding TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fallback_vectors_updated_at ON fallback_vectors (updated_at)"
        )
        conn.commit()
        self._fallback_conn = conn
        log.warning(
            "⚠️ 向量后端回退到 SQLite 模式 | path={} | reason={}",
            self._fallback_db_path,
            reason,
        )

    def _sqlite_fallback_items(self) -> List[Dict[str, Any]]:
        if self._fallback_conn is None:
            return []
        rows = self._fallback_conn.execute(
            "SELECT memory_id, document, metadata, embedding FROM fallback_vectors"
        ).fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "memory_id": row["memory_id"],
                    "document": row["document"] or "",
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "embedding": json.loads(row["embedding"] or "[]"),
                }
            )
        return items

    def _tokenize(self, text: str) -> List[str]:
        text = (text or "").lower()
        ascii_tokens = re.findall(r"[a-z0-9_]+", text)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)

        tokens: List[str] = []
        tokens.extend(ascii_tokens)
        tokens.extend(cjk_chars)

        if len(cjk_chars) >= 2:
            tokens.extend(
                "".join(cjk_chars[i : i + 2]) for i in range(len(cjk_chars) - 1)
            )

        if not tokens:
            tokens = list(text)
        return tokens

    def _expand_query_tokens(self, text: str) -> List[str]:
        base_tokens = self._tokenize(text)
        expanded: List[str] = []
        for token in base_tokens:
            if token not in expanded:
                expanded.append(token)
            for synonym in FALLBACK_QUERY_SYNONYMS.get(token, ()):
                normalized = synonym.lower()
                if normalized not in expanded:
                    expanded.append(normalized)
                    for nested in self._tokenize(normalized):
                        if nested not in expanded:
                            expanded.append(nested)
        return expanded or base_tokens

    def generate_embedding(self, text: str) -> List[float]:
        """
        生成文本的向量嵌入

        Args:
            text: 待嵌入的文本

        Returns:
            向量嵌入列表
        """
        if self.embedding_model is None:
            return self._generate_fallback_embedding(text)
        embedding = self.embedding_model.encode(
            text or "",
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embedding.astype(float).tolist()

    def _generate_fallback_embedding(self, text: str) -> List[float]:
        vector = [0.0] * self.embedding_dimension
        tokens = self._tokenize(text)
        if not tokens:
            return vector
        for token in tokens:
            index = hash(token) % self.embedding_dimension
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    @staticmethod
    def _cosine_similarity(left: List[float], right: List[float]) -> float:
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        dot = sum(left[i] * right[i] for i in range(size))
        left_norm = math.sqrt(sum(left[i] * left[i] for i in range(size))) or 1.0
        right_norm = math.sqrt(sum(right[i] * right[i] for i in range(size))) or 1.0
        return dot / (left_norm * right_norm)

    def _token_overlap_similarity(self, query: str, document: str) -> float:
        query_tokens = set(self._expand_query_tokens(query))
        document_tokens = set(self._tokenize(document))
        if not query_tokens or not document_tokens:
            return 0.0
        overlap = len(query_tokens & document_tokens)
        return overlap / math.sqrt(len(query_tokens) * len(document_tokens))

    def _bm25_similarity(
        self,
        *,
        query_tokens: List[str],
        document_tokens: List[str],
        corpus_size: int,
        avgdl: float,
        doc_freq: Dict[str, int],
    ) -> float:
        if not query_tokens or not document_tokens:
            return 0.0
        if avgdl <= 0:
            avgdl = 1.0

        k1 = 1.5
        b = 0.75
        score = 0.0
        document_length = len(document_tokens)
        unique_query_tokens = list(dict.fromkeys(query_tokens))
        for token in unique_query_tokens:
            term_freq = document_tokens.count(token)
            if term_freq == 0:
                continue
            token_doc_freq = doc_freq.get(token, 0)
            idf = math.log(
                1 + (corpus_size - token_doc_freq + 0.5) / (token_doc_freq + 0.5)
            )
            numerator = term_freq * (k1 + 1)
            denominator = term_freq + k1 * (1 - b + b * (document_length / avgdl))
            score += idf * (numerator / max(denominator, 1e-9))

        normalizer = max(len(unique_query_tokens), 1)
        return score / normalizer

    def _fallback_similarity(
        self,
        *,
        query_tokens: List[str],
        query_embedding: List[float],
        document: str,
        document_tokens: List[str],
        document_embedding: List[float],
        corpus_size: int,
        avgdl: float,
        doc_freq: Dict[str, int],
    ) -> float:
        cosine_similarity = self._cosine_similarity(query_embedding, document_embedding)
        lexical_similarity = self._token_overlap_similarity(
            " ".join(query_tokens), document
        )
        bm25_similarity = self._bm25_similarity(
            query_tokens=query_tokens,
            document_tokens=document_tokens,
            corpus_size=corpus_size,
            avgdl=avgdl,
            doc_freq=doc_freq,
        )
        keyword_bonus = 0.0
        lowered_document = document.lower()
        for token in query_tokens:
            if token and token in lowered_document:
                keyword_bonus += 0.015
        return max(
            cosine_similarity,
            lexical_similarity,
            bm25_similarity,
            cosine_similarity * 0.55
            + lexical_similarity * 0.25
            + bm25_similarity * 0.20
            + min(keyword_bonus, 0.12),
        )

    def add_memory(
        self, memory_id: str, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        添加记忆到向量数据库
        """
        try:
            embedding = self.generate_embedding(text)
            payload = metadata or {}

            if self.backend == "in_memory":
                self._memory_store[memory_id] = {
                    "embedding": embedding,
                    "document": text,
                    "metadata": payload,
                }
                return True

            if self.backend == "sqlite_fallback":
                if self._fallback_conn is None:
                    raise RuntimeError("SQLite fallback 尚未初始化")
                now = datetime.now().isoformat()
                self._fallback_conn.execute(
                    """
                    INSERT INTO fallback_vectors (memory_id, document, metadata, embedding, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        document=excluded.document,
                        metadata=excluded.metadata,
                        embedding=excluded.embedding,
                        updated_at=excluded.updated_at
                    """,
                    (
                        memory_id,
                        text,
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(embedding),
                        now,
                        now,
                    ),
                )
                self._fallback_conn.commit()
                return True

            if self.collection is None:
                raise RuntimeError("Chroma collection 尚未初始化")
            self.collection.upsert(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[payload],
            )

            log.debug(f"➕ 添加记忆到向量库: id={memory_id}, backend={self.backend}")
            return True

        except Exception as e:
            log.error(f"❌ 添加记忆失败: {e}")
            return False

    def search_similar(
        self, query: str, top_k: int = 5, min_similarity: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        搜索相似的记忆
        """
        try:
            if min_similarity is None:
                min_similarity = MEMORY_RETRIEVAL_SIMILARITY_THRESHOLD

            query_embedding = self.generate_embedding(query)
            query_tokens = self._expand_query_tokens(query)

            if self.backend == "in_memory":
                matches = []
                prepared_items = []
                doc_freq: Dict[str, int] = {}
                total_doc_length = 0
                for memory_id, item in self._memory_store.items():
                    document_tokens = self._tokenize(item.get("document", ""))
                    total_doc_length += len(document_tokens)
                    for token in set(document_tokens):
                        doc_freq[token] = doc_freq.get(token, 0) + 1
                    prepared_items.append((memory_id, item, document_tokens))
                corpus_size = max(len(prepared_items), 1)
                avgdl = total_doc_length / corpus_size if total_doc_length else 1.0
                for memory_id, item, document_tokens in prepared_items:
                    similarity = self._fallback_similarity(
                        query_tokens=query_tokens,
                        query_embedding=query_embedding,
                        document=item.get("document", ""),
                        document_tokens=document_tokens,
                        document_embedding=item.get("embedding", []),
                        corpus_size=corpus_size,
                        avgdl=avgdl,
                        doc_freq=doc_freq,
                    )
                    if similarity < min_similarity:
                        continue
                    matches.append(
                        {
                            "id": memory_id,
                            "document": item.get("document", ""),
                            "metadata": item.get("metadata", {}),
                            "similarity": similarity,
                        }
                    )
                matches.sort(key=lambda item: item["similarity"], reverse=True)
                return matches[:top_k]

            if self.backend == "sqlite_fallback":
                matches = []
                items = self._sqlite_fallback_items()
                prepared_items = []
                doc_freq: Dict[str, int] = {}
                total_doc_length = 0
                for item in items:
                    document_tokens = self._tokenize(item.get("document", ""))
                    total_doc_length += len(document_tokens)
                    for token in set(document_tokens):
                        doc_freq[token] = doc_freq.get(token, 0) + 1
                    prepared_items.append((item, document_tokens))
                corpus_size = max(len(prepared_items), 1)
                avgdl = total_doc_length / corpus_size if total_doc_length else 1.0
                for item, document_tokens in prepared_items:
                    similarity = self._fallback_similarity(
                        query_tokens=query_tokens,
                        query_embedding=query_embedding,
                        document=item.get("document", ""),
                        document_tokens=document_tokens,
                        document_embedding=item.get("embedding", []),
                        corpus_size=corpus_size,
                        avgdl=avgdl,
                        doc_freq=doc_freq,
                    )
                    if similarity < min_similarity:
                        continue
                    matches.append(
                        {
                            "id": item.get("memory_id"),
                            "document": item.get("document", ""),
                            "metadata": item.get("metadata", {}),
                            "similarity": similarity,
                        }
                    )
                matches.sort(key=lambda item: item["similarity"], reverse=True)
                return matches[:top_k]

            if self.collection is None:
                raise RuntimeError("Chroma collection 尚未初始化")
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )

            matches = []
            if results["ids"] and results["ids"][0]:
                for i, memory_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results["distances"] else 0
                    similarity = 1 - distance
                    if similarity >= min_similarity:
                        matches.append(
                            {
                                "id": memory_id,
                                "document": results["documents"][0][i]
                                if results["documents"]
                                else "",
                                "metadata": results["metadatas"][0][i]
                                if results["metadatas"]
                                else {},
                                "similarity": similarity,
                            }
                        )
            log.debug(f"🔍 向量检索到 {len(matches)} 条相似记忆")
            return matches

        except Exception as e:
            log.error(f"❌ 向量检索失败: {e}")
            return []

    def update_memory(
        self, memory_id: str, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """更新记忆的向量表示"""
        try:
            if self.backend == "in_memory":
                self._memory_store.pop(memory_id, None)
                return self.add_memory(memory_id, text, metadata)

            if self.backend == "sqlite_fallback":
                if self._fallback_conn is None:
                    raise RuntimeError("SQLite fallback 尚未初始化")
                self._fallback_conn.execute(
                    "DELETE FROM fallback_vectors WHERE memory_id = ?", (memory_id,)
                )
                self._fallback_conn.commit()
                return self.add_memory(memory_id, text, metadata)

            if self.collection is None:
                raise RuntimeError("Chroma collection 尚未初始化")
            self.collection.delete(ids=[memory_id])

            return self.add_memory(memory_id, text, metadata)
        except Exception as e:
            log.error(f"❌ 更新记忆向量失败: {e}")
            return False

    def delete_memory(self, memory_id: str) -> bool:
        """从向量数据库删除记忆"""
        try:
            if self.backend == "in_memory":
                return self._memory_store.pop(memory_id, None) is not None

            if self.backend == "sqlite_fallback":
                if self._fallback_conn is None:
                    raise RuntimeError("SQLite fallback 尚未初始化")
                cursor = self._fallback_conn.execute(
                    "DELETE FROM fallback_vectors WHERE memory_id = ?", (memory_id,)
                )
                self._fallback_conn.commit()
                return bool(cursor.rowcount)

            if self.collection is None:
                raise RuntimeError("Chroma collection 尚未初始化")
            self.collection.delete(ids=[memory_id])

            log.debug(f"🗑️ 从向量库删除记忆: id={memory_id}, backend={self.backend}")
            return True
        except Exception as e:
            log.error(f"❌ 删除记忆向量失败: {e}")
            return False

    def get_memory_count(self) -> int:
        """获取向量库中的记忆数量"""
        try:
            if self.backend == "in_memory":
                return len(self._memory_store)

            if self.backend == "sqlite_fallback":
                if self._fallback_conn is None:
                    raise RuntimeError("SQLite fallback 尚未初始化")
                row = self._fallback_conn.execute(
                    "SELECT COUNT(*) FROM fallback_vectors"
                ).fetchone()
                return int(row[0]) if row else 0

            if self.collection is None:
                raise RuntimeError("Chroma collection 尚未初始化")
            return self.collection.count()
        except Exception as e:
            log.error(f"❌ 获取记忆数量失败: {e}")
            return 0

    def clear_all(self) -> bool:
        """清空所有记忆（测试用）"""
        try:
            if self.backend == "in_memory":
                self._memory_store.clear()
                return True

            if self.backend == "sqlite_fallback":
                if self._fallback_conn is None:
                    raise RuntimeError("SQLite fallback 尚未初始化")
                self._fallback_conn.execute("DELETE FROM fallback_vectors")
                self._fallback_conn.commit()
                return True

            if self.client is None:
                raise RuntimeError("Chroma client 尚未初始化")
            self.client.delete_collection("memories")
            self.collection = self.client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
            log.warning(f"🧹 向量库已清空，backend={self.backend}")
            return True
        except Exception as e:
            log.error(f"❌ 清空向量库失败: {e}")
            return False


class HybridRetriever:
    """混合检索器（关键词+向量）"""

    def __init__(self, vector_retriever: VectorRetriever, database_manager: Any):
        """
        初始化混合检索器

        Args:
            vector_retriever: 向量检索器
            database_manager: 数据库管理器
        """
        self.vector_retriever = vector_retriever
        self.database_manager = database_manager

    def _calculate_dynamic_threshold(self) -> float:
        """根据记忆库密度动态调整检索阈值"""
        total_memories = max(0, self.database_manager.count_memories())
        dynamic_threshold = (
            MEMORY_RETRIEVAL_SIMILARITY_THRESHOLD
            + total_memories * MEMORY_RETRIEVAL_THRESHOLD_DENSITY_FACTOR / 100
        )
        return max(
            MEMORY_RETRIEVAL_THRESHOLD_MIN,
            min(MEMORY_RETRIEVAL_THRESHOLD_MAX, dynamic_threshold),
        )

    def _tokenize(self, text: str) -> List[str]:
        return self.vector_retriever._tokenize(text)

    def _keyword_fallback_search(self, query: str, limit: int) -> List[Any]:
        """当整串关键词未命中时，使用轻量分词重排数据库结果。"""
        tokens = list(dict.fromkeys(self._tokenize(query)))
        if not tokens:
            return []

        candidates = self.database_manager.get_recent_memories(
            hours=24 * 365, limit=1000
        )
        scored = []
        for memory in candidates:
            haystack = " ".join(
                filter(None, [memory.event, memory.thought, memory.lesson])
            ).lower()
            score = sum(1 for token in tokens if token and token in haystack)
            if score > 0:
                scored.append((score, memory.importance, memory.timestamp, memory))

        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in scored[:limit]]

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        混合搜索（向量+关键词）

        策略：
        1. 先进行向量检索，获取语义相似的记忆
        2. 再进行关键词检索，获取精确匹配的记忆
        3. 合并结果，去重，按综合得分排序

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            混合检索结果
        """
        results = {}
        min_similarity = self._calculate_dynamic_threshold()

        vector_results = self.vector_retriever.search_similar(
            query,
            top_k=top_k * 2,
            min_similarity=min_similarity,
        )
        for result in vector_results:
            memory_id = result["id"]
            results[memory_id] = {
                "id": memory_id,
                "document": result["document"],
                "similarity": result["similarity"],
                "vector_score": result["similarity"],
                "keyword_score": 0.0,
                "combined_score": 0.0,
            }

        keyword_results = self.database_manager.search_memories(
            query=query, limit=top_k * 2, min_importance=0.0
        )
        if not keyword_results:
            keyword_results = self._keyword_fallback_search(query, limit=top_k * 2)

        for memory in keyword_results:
            memory_id = str(memory.id)
            if memory_id not in results:
                results[memory_id] = {
                    "id": memory_id,
                    "document": memory.event,
                    "similarity": 0.0,
                    "vector_score": 0.0,
                    "keyword_score": 1.0,
                    "combined_score": 0.0,
                }
            else:
                results[memory_id]["keyword_score"] = 1.0

        for result in results.values():
            result["combined_score"] = (
                result["vector_score"] * 0.7 + result["keyword_score"] * 0.3
            )

        sorted_results = sorted(
            results.values(), key=lambda x: x["combined_score"], reverse=True
        )
        final_results = sorted_results[:top_k]

        log.debug(f"🔍 混合检索到 {len(final_results)} 条记忆")
        return final_results
