"""记忆存储模块"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import uuid
from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from .models import Base, MemoryEntry, MemorySummary, SemanticWisdom, build_source_hash
from config.settings import settings
from src.utils.logger import log
from src.utils.helpers import exponential_decay, calculate_similarity


class DatabaseManager:
    """数据库管理器"""

    WISDOM_TEXT_SIMILARITY_THRESHOLD = 0.9

    def __init__(self, database_url: Optional[str] = None):
        """
        初始化数据库管理器

        Args:
            database_url: 数据库连接URL，默认从配置读取
        """
        self.database_url = database_url or settings.DATABASE_URL
        self.engine = create_engine(self.database_url, echo=False)
        self.SessionLocal = sessionmaker(
            bind=self.engine, autocommit=False, autoflush=False
        )

        # 创建所有表
        Base.metadata.create_all(bind=self.engine)
        self._ensure_semantic_wisdom_columns()
        log.info(f"📁 记忆数据库已初始化: {self.database_url}")

    def get_session(self) -> Session:
        """获取数据库会话"""
        return self.SessionLocal()

    def _ensure_semantic_wisdom_columns(self) -> None:
        """为旧数据库补齐 semantic_wisdom 新增列。"""
        try:
            inspector = inspect(self.engine)
            if "semantic_wisdom" not in inspector.get_table_names():
                return

            existing_columns = {
                column["name"] for column in inspector.get_columns("semantic_wisdom")
            }
            statements = []
            if "category" not in existing_columns:
                statements.append(
                    "ALTER TABLE semantic_wisdom ADD COLUMN category VARCHAR(50) NOT NULL DEFAULT 'learning'"
                )
            if "x" not in existing_columns:
                statements.append(
                    "ALTER TABLE semantic_wisdom ADD COLUMN x FLOAT NOT NULL DEFAULT 0.0"
                )
            if "y" not in existing_columns:
                statements.append(
                    "ALTER TABLE semantic_wisdom ADD COLUMN y FLOAT NOT NULL DEFAULT 0.0"
                )
            if "z" not in existing_columns:
                statements.append(
                    "ALTER TABLE semantic_wisdom ADD COLUMN z FLOAT NOT NULL DEFAULT 4.0"
                )
            if "gravity" not in existing_columns:
                statements.append(
                    "ALTER TABLE semantic_wisdom ADD COLUMN gravity FLOAT NOT NULL DEFAULT 0.5"
                )

            if not statements:
                return

            with self.engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))
            log.info("🧭 semantic_wisdom 旧表已补齐空间字段")
        except Exception as e:
            log.warning(f"⚠️ 补齐 semantic_wisdom 列失败: {e}")

    def create_memory(
        self,
        event: str,
        thought: Optional[str] = None,
        emotion: Optional[Dict[str, float]] = None,
        lesson: Optional[str] = None,
        importance: float = 0.5,
        embedding: Optional[List[float]] = None,
    ) -> Optional[MemoryEntry]:
        """
        创建新的记忆条目

        Args:
            event: 事件描述
            thought: 当时的想法
            emotion: 情绪状态
            lesson: 学到的教训
            importance: 重要性评分（0-1）
            embedding: 向量嵌入

        Returns:
            创建的记忆条目，失败返回None
        """
        session = self.get_session()
        try:
            memory = MemoryEntry(
                event=event,
                thought=thought,
                emotion=emotion,
                lesson=lesson,
                importance=importance,
                embedding=embedding,
                sync_transaction_id=uuid.uuid4().hex,
            )

            session.add(memory)
            session.commit()
            session.refresh(memory)

            log.debug(f"💾 创建记忆条目: id={memory.id}, importance={importance}")
            return memory

        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 创建记忆失败: {e}")
            return None
        finally:
            session.close()

    def get_memory_by_id(self, memory_id: int) -> Optional[MemoryEntry]:
        """
        根据ID获取记忆条目

        Args:
            memory_id: 记忆ID

        Returns:
            记忆条目，未找到返回None
        """
        session = self.get_session()
        try:
            memory = (
                session.query(MemoryEntry)
                .filter(MemoryEntry.id == memory_id, MemoryEntry.is_deleted.is_(False))
                .first()
            )

            if memory:
                # 更新访问统计
                memory.access_count += 1
                memory.last_accessed = datetime.now()
                session.commit()
                session.refresh(memory)

            return memory

        except SQLAlchemyError as e:
            log.error(f"❌ 获取记忆失败: {e}")
            return None
        finally:
            session.close()

    def search_memories(
        self,
        query: str,
        limit: int = 10,
        min_importance: float = 0.0,
        time_range: Optional[tuple] = None,
    ) -> List[MemoryEntry]:
        """
        搜索记忆条目（基于关键词和重要性）

        Args:
            query: 搜索关键词
            limit: 返回数量限制
            min_importance: 最低重要性
            time_range: 时间范围 (start_time, end_time)

        Returns:
            匹配的记忆条目列表
        """
        session = self.get_session()
        try:
            # 构建查询
            query_obj = session.query(MemoryEntry).filter(
                MemoryEntry.importance >= min_importance,
                MemoryEntry.is_deleted.is_(False),
            )

            # 关键词搜索（事件或想法字段）
            if query:
                query_obj = query_obj.filter(
                    (MemoryEntry.event.like(f"%{query}%"))
                    | (MemoryEntry.thought.like(f"%{query}%"))
                    | (MemoryEntry.lesson.like(f"%{query}%"))
                )

            # 时间范围过滤
            if time_range:
                start_time, end_time = time_range
                query_obj = query_obj.filter(
                    MemoryEntry.timestamp >= start_time,
                    MemoryEntry.timestamp <= end_time,
                )

            # 排序：按重要性降序，然后按时间降序
            query_obj = query_obj.order_by(
                MemoryEntry.importance.desc(), MemoryEntry.timestamp.desc()
            )

            # 限制数量
            memories = query_obj.limit(limit).all()
            log.debug(f"🔍 搜索到 {len(memories)} 条记忆")

            return memories

        except SQLAlchemyError as e:
            log.error(f"❌ 搜索记忆失败: {e}")
            return []
        finally:
            session.close()

    def list_memories(self, limit: int = 100000) -> List[MemoryEntry]:
        """列出所有未删除记忆，用于全量重索引。"""
        session = self.get_session()
        try:
            return (
                session.query(MemoryEntry)
                .filter(MemoryEntry.is_deleted.is_(False))
                .order_by(MemoryEntry.timestamp.asc(), MemoryEntry.id.asc())
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            log.error(f"❌ 列出记忆失败: {e}")
            return []
        finally:
            session.close()

    def update_memory(self, memory_id: int, **kwargs) -> bool:
        """
        更新记忆条目

        Args:
            memory_id: 记忆ID
            **kwargs: 要更新的字段

        Returns:
            是否更新成功
        """
        session = self.get_session()
        try:
            memory = (
                session.query(MemoryEntry)
                .filter(MemoryEntry.id == memory_id, MemoryEntry.is_deleted.is_(False))
                .first()
            )

            if not memory:
                log.warning(f"⚠️ 记忆不存在: id={memory_id}")
                return False

            # 更新字段
            for key, value in kwargs.items():
                if hasattr(memory, key):
                    setattr(memory, key, value)

            session.commit()
            log.debug(f"✏️ 更新记忆: id={memory_id}")
            return True

        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 更新记忆失败: {e}")
            return False
        finally:
            session.close()

    def delete_memory(self, memory_id: int) -> bool:
        """
        删除记忆条目

        Args:
            memory_id: 记忆ID

        Returns:
            是否删除成功
        """
        session = self.get_session()
        try:
            memory = (
                session.query(MemoryEntry)
                .filter(MemoryEntry.id == memory_id, MemoryEntry.is_deleted.is_(False))
                .first()
            )

            if not memory:
                log.warning(f"⚠️ 记忆不存在: id={memory_id}")
                return False

            memory.is_deleted = True
            memory.deleted_at = datetime.now()
            memory.sync_transaction_id = uuid.uuid4().hex
            session.commit()
            log.debug(f"🪦 逻辑删除记忆: id={memory_id}")
            return True

        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 删除记忆失败: {e}")
            return False
        finally:
            session.close()

    def get_memory_any_state(self, memory_id: int) -> Optional[MemoryEntry]:
        session = self.get_session()
        try:
            return (
                session.query(MemoryEntry).filter(MemoryEntry.id == memory_id).first()
            )
        except SQLAlchemyError as e:
            log.error(f"❌ 获取任意状态记忆失败: {e}")
            return None
        finally:
            session.close()

    def get_recent_memories(
        self, hours: int = 24, limit: int = 20, memory_type: Optional[str] = None
    ) -> List[MemoryEntry]:
        """
        获取最近的记忆

        Args:
            hours: 最近多少小时
            limit: 返回数量限制
            memory_type: 可选的记忆类型过滤

        Returns:
            最近的记忆列表
        """
        session = self.get_session()
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            query = session.query(MemoryEntry).filter(
                MemoryEntry.timestamp >= cutoff_time,
                MemoryEntry.is_deleted.is_(False),
            )
            if memory_type:
                query = query.filter(MemoryEntry.memory_type == memory_type)

            memories = query.order_by(MemoryEntry.timestamp.desc()).limit(limit).all()

            log.debug(f"🕒 获取最近 {len(memories)} 条记忆")
            return memories

        except SQLAlchemyError as e:
            log.error(f"❌ 获取最近记忆失败: {e}")
            return []
        finally:
            session.close()

    def get_important_memories(
        self, min_importance: float = 0.7, limit: int = 10
    ) -> List[MemoryEntry]:
        """
        获取重要的记忆

        Args:
            min_importance: 最低重要性阈值
            limit: 返回数量限制

        Returns:
            重要的记忆列表
        """
        session = self.get_session()
        try:
            memories = (
                session.query(MemoryEntry)
                .filter(
                    MemoryEntry.importance >= min_importance,
                    MemoryEntry.is_deleted.is_(False),
                )
                .order_by(MemoryEntry.importance.desc())
                .limit(limit)
                .all()
            )

            log.debug(f"⭐ 获取 {len(memories)} 条重要记忆")
            return memories

        except SQLAlchemyError as e:
            log.error(f"❌ 获取重要记忆失败: {e}")
            return []
        finally:
            session.close()

    def count_memories(self, memory_type: Optional[str] = None) -> int:
        """
        统计记忆总数

        Returns:
            记忆总数
        """
        session = self.get_session()
        try:
            query = session.query(func.count(MemoryEntry.id)).filter(
                MemoryEntry.is_deleted.is_(False)
            )
            if memory_type:
                query = query.filter(MemoryEntry.memory_type == memory_type)
            count = query.scalar()
            return count or 0
        except SQLAlchemyError as e:
            log.error(f"❌ 统计记忆失败: {e}")
            return 0
        finally:
            session.close()

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取记忆统计信息

        Returns:
            统计信息字典
        """
        session = self.get_session()
        try:
            total = (
                session.query(func.count(MemoryEntry.id))
                .filter(MemoryEntry.is_deleted.is_(False))
                .scalar()
                or 0
            )
            avg_importance = (
                session.query(func.avg(MemoryEntry.importance))
                .filter(MemoryEntry.is_deleted.is_(False))
                .scalar()
                or 0
            )

            # 按类型统计
            type_counts = (
                session.query(MemoryEntry.memory_type, func.count(MemoryEntry.id))
                .filter(MemoryEntry.is_deleted.is_(False))
                .group_by(MemoryEntry.memory_type)
                .all()
            )
            wisdom_count = (
                session.query(func.count(SemanticWisdom.id))
                .filter(SemanticWisdom.is_deleted.is_(False))
                .scalar()
                or 0
            )

            stats = {
                "total_memories": total,
                "average_importance": round(avg_importance, 2),
                "memory_types": {t: c for t, c in type_counts},
                "semantic_wisdom_count": wisdom_count,
            }

            return stats

        except SQLAlchemyError as e:
            log.error(f"❌ 获取统计信息失败: {e}")
            return {}
        finally:
            session.close()

    def create_semantic_wisdom(
        self,
        wisdom_text: str,
        source_memory_ids: List[int],
        source_sync_transaction_ids: Optional[List[str]] = None,
        trigger_type: str = "capacity",
        style: str = "aphorism",
        importance: float = 0.5,
        category: str = "learning",
        x: float = 0.0,
        y: float = 0.0,
        z: float = 4.0,
        gravity: float = 0.5,
        embedding: Optional[List[float]] = None,
        force_new: bool = False,
    ) -> Optional[SemanticWisdom]:
        session = self.get_session()
        try:
            source_hash = build_source_hash(source_memory_ids)
            existing = (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.source_hash == source_hash,
                    SemanticWisdom.is_deleted.is_(False),
                )
                .first()
            )
            if existing:
                setattr(existing, "_was_created", False)
                setattr(existing, "_dedup_reason", "source_hash")
                return existing

            if not force_new:
                existing = self._find_active_wisdom_by_text(session, wisdom_text)
                if existing:
                    updated = self._upsert_existing_semantic_wisdom(
                        session=session,
                        wisdom=existing,
                        source_memory_ids=source_memory_ids,
                        source_sync_transaction_ids=source_sync_transaction_ids,
                        importance=importance,
                        gravity=gravity,
                    )
                    setattr(updated, "_was_created", False)
                    setattr(updated, "_dedup_reason", "exact_text")
                    return updated

                existing = self._find_similar_active_wisdom(session, wisdom_text)
                if existing:
                    updated = self._upsert_existing_semantic_wisdom(
                        session=session,
                        wisdom=existing,
                        source_memory_ids=source_memory_ids,
                        source_sync_transaction_ids=source_sync_transaction_ids,
                        importance=importance,
                        gravity=gravity,
                    )
                    setattr(updated, "_was_created", False)
                    setattr(updated, "_dedup_reason", "similarity")
                    return updated

            wisdom = SemanticWisdom(
                wisdom_text=wisdom_text,
                style=style,
                source_memory_ids=source_memory_ids,
                source_sync_transaction_ids=source_sync_transaction_ids or [],
                source_hash=source_hash,
                trigger_type=trigger_type,
                importance=importance,
                category=category,
                x=x,
                y=y,
                z=z,
                gravity=gravity,
                embedding=embedding,
                sync_transaction_id=uuid.uuid4().hex,
            )
            session.add(wisdom)
            session.commit()
            session.refresh(wisdom)
            setattr(wisdom, "_was_created", True)
            setattr(
                wisdom, "_dedup_reason", None if not force_new else "force_new_node"
            )
            return wisdom
        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 创建语义智慧失败: {e}")
            return None
        finally:
            session.close()

    def _find_active_wisdom_by_text(
        self, session: Session, wisdom_text: str
    ) -> Optional[SemanticWisdom]:
        return (
            session.query(SemanticWisdom)
            .filter(
                SemanticWisdom.wisdom_text == wisdom_text,
                SemanticWisdom.is_deleted.is_(False),
            )
            .order_by(
                SemanticWisdom.importance.desc(),
                SemanticWisdom.created_at.desc(),
                SemanticWisdom.id.desc(),
            )
            .first()
        )

    def _find_similar_active_wisdom(
        self, session: Session, wisdom_text: str
    ) -> Optional[SemanticWisdom]:
        candidates = (
            session.query(SemanticWisdom)
            .filter(SemanticWisdom.is_deleted.is_(False))
            .order_by(
                SemanticWisdom.importance.desc(),
                SemanticWisdom.created_at.desc(),
                SemanticWisdom.id.desc(),
            )
            .all()
        )
        best_match = None
        best_score = self.WISDOM_TEXT_SIMILARITY_THRESHOLD
        for candidate in candidates:
            similarity = calculate_similarity(wisdom_text, candidate.wisdom_text)
            if similarity > best_score:
                best_score = similarity
                best_match = candidate
        return best_match

    def _upsert_existing_semantic_wisdom(
        self,
        session: Session,
        wisdom: SemanticWisdom,
        source_memory_ids: List[int],
        source_sync_transaction_ids: Optional[List[str]],
        importance: float,
        gravity: float,
    ) -> SemanticWisdom:
        wisdom.importance = max(wisdom.importance or 0.0, importance)
        wisdom.created_at = datetime.now()
        wisdom.source_memory_ids = sorted(
            {*(wisdom.source_memory_ids or []), *source_memory_ids}
        )
        wisdom.source_sync_transaction_ids = sorted(
            {
                *(wisdom.source_sync_transaction_ids or []),
                *(source_sync_transaction_ids or []),
            }
        )
        wisdom.gravity = max(wisdom.gravity or 0.0, gravity)
        session.commit()
        session.refresh(wisdom)
        return wisdom

    def get_semantic_wisdom_by_id(self, wisdom_id: int) -> Optional[SemanticWisdom]:
        session = self.get_session()
        try:
            return (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.id == wisdom_id, SemanticWisdom.is_deleted.is_(False)
                )
                .first()
            )
        except SQLAlchemyError as e:
            log.error(f"❌ 获取语义智慧失败: {e}")
            return None
        finally:
            session.close()

    def search_semantic_wisdom(
        self, query: str, limit: int = 10
    ) -> List[SemanticWisdom]:
        session = self.get_session()
        try:
            query_obj = session.query(SemanticWisdom).filter(
                SemanticWisdom.is_deleted.is_(False)
            )
            if query:
                query_obj = query_obj.filter(
                    SemanticWisdom.wisdom_text.like(f"%{query}%")
                )
            return (
                query_obj.order_by(
                    SemanticWisdom.importance.desc(), SemanticWisdom.created_at.desc()
                )
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            log.error(f"❌ 搜索语义智慧失败: {e}")
            return []
        finally:
            session.close()

    def list_semantic_wisdom(self, limit: int = 1000) -> List[SemanticWisdom]:
        session = self.get_session()
        try:
            return (
                session.query(SemanticWisdom)
                .filter(SemanticWisdom.is_deleted.is_(False))
                .order_by(SemanticWisdom.created_at.desc())
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            log.error(f"❌ 列出语义智慧失败: {e}")
            return []
        finally:
            session.close()

    def update_semantic_wisdom(self, wisdom_id: int, **kwargs) -> bool:
        """更新语义智慧字段，用于本地重索引后回写 embedding 与坐标。"""
        session = self.get_session()
        try:
            wisdom = (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.id == wisdom_id,
                    SemanticWisdom.is_deleted.is_(False),
                )
                .first()
            )
            if not wisdom:
                return False

            for key, value in kwargs.items():
                if hasattr(wisdom, key):
                    setattr(wisdom, key, value)
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 更新语义智慧失败: {e}")
            return False
        finally:
            session.close()

    def soft_delete_semantic_wisdom(self, wisdom_id: int) -> bool:
        session = self.get_session()
        try:
            wisdom = (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.id == wisdom_id, SemanticWisdom.is_deleted.is_(False)
                )
                .first()
            )
            if not wisdom:
                return False

            wisdom.is_deleted = True
            wisdom.deleted_at = datetime.now()
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 删除语义智慧失败: {e}")
            return False
        finally:
            session.close()

    def soft_delete_semantic_wisdom_by_source_memory_id(
        self, memory_id: int
    ) -> List[int]:
        session = self.get_session()
        try:
            wisdom_entries = (
                session.query(SemanticWisdom)
                .filter(SemanticWisdom.is_deleted.is_(False))
                .all()
            )
            deleted_at = datetime.now()
            deleted_ids: List[int] = []
            for wisdom in wisdom_entries:
                source_memory_ids = [
                    int(item) for item in (wisdom.source_memory_ids or [])
                ]
                if memory_id not in source_memory_ids:
                    continue
                wisdom.is_deleted = True
                wisdom.deleted_at = deleted_at
                if wisdom.id is not None:
                    deleted_ids.append(int(wisdom.id))
            if deleted_ids:
                session.commit()
            return deleted_ids
        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 按来源记忆删除语义智慧失败: {e}")
            return []
        finally:
            session.close()

    def soft_delete_semantic_wisdom_by_text(self, wisdom_text: str) -> int:
        session = self.get_session()
        try:
            wisdom_entries = (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.wisdom_text == wisdom_text,
                    SemanticWisdom.is_deleted.is_(False),
                )
                .all()
            )
            if not wisdom_entries:
                return 0

            deleted_at = datetime.now()
            for wisdom in wisdom_entries:
                wisdom.is_deleted = True
                wisdom.deleted_at = deleted_at
            session.commit()
            return len(wisdom_entries)
        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 按文本删除语义智慧失败: {e}")
            return 0
        finally:
            session.close()

    def soft_delete_duplicate_semantic_wisdom_by_text(self, wisdom_text: str) -> int:
        session = self.get_session()
        try:
            wisdom_entries = (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.wisdom_text == wisdom_text,
                    SemanticWisdom.is_deleted.is_(False),
                )
                .order_by(
                    SemanticWisdom.importance.desc(),
                    SemanticWisdom.created_at.desc(),
                    SemanticWisdom.id.desc(),
                )
                .all()
            )
            if len(wisdom_entries) <= 1:
                return 0

            deleted_at = datetime.now()
            for wisdom in wisdom_entries[1:]:
                wisdom.is_deleted = True
                wisdom.deleted_at = deleted_at
            session.commit()
            return len(wisdom_entries) - 1
        except SQLAlchemyError as e:
            session.rollback()
            log.error(f"❌ 按文本去重语义智慧失败: {e}")
            return 0
        finally:
            session.close()

    def find_wisdom_by_source_hash(self, source_hash: str) -> Optional[SemanticWisdom]:
        session = self.get_session()
        try:
            return (
                session.query(SemanticWisdom)
                .filter(
                    SemanticWisdom.source_hash == source_hash,
                    SemanticWisdom.is_deleted.is_(False),
                )
                .first()
            )
        except SQLAlchemyError as e:
            log.error(f"❌ 根据来源哈希查询语义智慧失败: {e}")
            return None
        finally:
            session.close()

    def count_semantic_wisdom(self) -> int:
        session = self.get_session()
        try:
            count = (
                session.query(func.count(SemanticWisdom.id))
                .filter(SemanticWisdom.is_deleted.is_(False))
                .scalar()
            )
            return count or 0
        except SQLAlchemyError as e:
            log.error(f"❌ 统计语义智慧失败: {e}")
            return 0
        finally:
            session.close()


class MemoryCompressor:
    """记忆压缩器"""

    @staticmethod
    def calculate_importance_decay(
        base_importance: float, hours_since_creation: float, decay_rate: float = 0.01
    ) -> float:
        """
        计算记忆重要性的衰减

        使用指数衰减模型

        Args:
            base_importance: 基础重要性
            hours_since_creation: 距离创建的小时数
            decay_rate: 衰减率（每小时）

        Returns:
            衰减后的重要性
        """
        return exponential_decay(
            base_importance, decay_rate, hours_since_creation / 24
        )  # 转换为天

    @staticmethod
    def should_compress(
        memory: MemoryEntry, current_time: Optional[datetime] = None
    ) -> bool:
        """
        判断记忆是否需要压缩

        条件：
        1. 重要性较低（< 0.3）
        2. 创建时间超过7天
        3. 访问次数很少（< 3次）

        Args:
            memory: 记忆条目
            current_time: 当前时间

        Returns:
            是否需要压缩
        """
        if current_time is None:
            current_time = datetime.now()

        # 计算距离创建的天数
        days_since_creation = (current_time - memory.timestamp).days

        # 判断条件
        if (
            memory.importance < 0.3
            and days_since_creation > 7
            and memory.access_count < 3
        ):
            return True

        return False
