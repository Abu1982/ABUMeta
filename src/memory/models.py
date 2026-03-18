"""记忆数据模型模块"""

from datetime import datetime
from typing import Optional, Dict, Any
import hashlib
import uuid
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def build_source_hash(source_memory_ids: Optional[list[int]]) -> str:
    """基于来源记忆ID构建稳定哈希。"""
    ids = source_memory_ids or []
    normalized = ",".join(str(memory_id) for memory_id in sorted(ids))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MemoryEntry(Base):
    """
    记忆条目模型

    每个记忆条目包含：
    - 事件描述
    - 当时的想法
    - 情绪状态
    - 学到的教训
    - 重要性评分
    - 向量嵌入（用于语义检索）
    """

    __tablename__ = "memory_entries"

    __tablename__ = "memory_entries"

    # 基本字段
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.now, nullable=False, index=True)

    # 记忆内容
    event = Column(Text, nullable=False)          # 发生了什么
    thought = Column(Text, nullable=True)         # 当时怎么想的
    emotion = Column(JSON, nullable=True)         # 情绪状态（字典）
    lesson = Column(Text, nullable=True)          # 学到的教训

    # 元数据
    importance = Column(Float, default=0.5)       # 重要性评分（0-1）
    embedding = Column(JSON, nullable=True)       # 向量嵌入
    memory_type = Column(String(50), default="episodic")  # 记忆类型：情景/语义

    # 同步与删除状态
    sync_transaction_id = Column(String(64), default=lambda: uuid.uuid4().hex, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)

    # 统计信息
    access_count = Column(Integer, default=0)     # 被访问次数
    last_accessed = Column(DateTime, nullable=True)  # 最后访问时间

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.memory_type is None:
            self.memory_type = "episodic"
        if self.sync_transaction_id is None:
            self.sync_transaction_id = uuid.uuid4().hex
        if self.is_deleted is None:
            self.is_deleted = False
        if self.access_count is None:
            self.access_count = 0

    def __repr__(self):
        return f"<MemoryEntry(id={self.id}, timestamp={self.timestamp}, importance={self.importance})>"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event": self.event,
            "thought": self.thought,
            "emotion": self.emotion,
            "lesson": self.lesson,
            "importance": self.importance,
            "memory_type": self.memory_type,
            "sync_transaction_id": self.sync_transaction_id,
            "is_deleted": self.is_deleted,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """从字典创建实例"""
        entry = cls(
            event=data["event"],
            thought=data.get("thought"),
            emotion=data.get("emotion"),
            lesson=data.get("lesson"),
            importance=data.get("importance", 0.5),
            memory_type=data.get("memory_type", "episodic"),
            sync_transaction_id=data.get("sync_transaction_id", uuid.uuid4().hex),
            is_deleted=data.get("is_deleted", False),
        )

        # 处理时间戳
        if "timestamp" in data:
            from datetime import datetime
            entry.timestamp = datetime.fromisoformat(data["timestamp"])

        return entry


class MemorySummary(Base):
    """
    记忆摘要模型

    用于存储长期记忆的摘要信息，减少存储空间
    """

    __tablename__ = "memory_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    summary_text = Column(Text, nullable=False)      # 摘要文本
    related_memory_ids = Column(JSON, nullable=True) # 相关记忆ID列表
    importance = Column(Float, default=0.5)          # 重要性

    def __repr__(self):
        return f"<MemorySummary(id={self.id}, importance={self.importance})>"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "summary_text": self.summary_text,
            "related_memory_ids": self.related_memory_ids,
            "importance": self.importance,
        }


class SemanticWisdom(Base):
    """高密度语义智慧条目。"""

    __tablename__ = "semantic_wisdom"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    wisdom_text = Column(Text, nullable=False)
    style = Column(String(50), default="aphorism", nullable=False)
    source_memory_ids = Column(JSON, nullable=False)
    source_sync_transaction_ids = Column(JSON, nullable=True)
    source_hash = Column(String(64), nullable=False, unique=True, index=True)
    trigger_type = Column(String(50), default="capacity", nullable=False)
    importance = Column(Float, default=0.5)
    category = Column(String(50), default="learning", nullable=False, index=True)
    x = Column(Float, default=0.0, nullable=False)
    y = Column(Float, default=0.0, nullable=False)
    z = Column(Float, default=4.0, nullable=False)
    gravity = Column(Float, default=0.5, nullable=False)
    embedding = Column(JSON, nullable=True)
    sync_transaction_id = Column(String(64), default=lambda: uuid.uuid4().hex, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.source_hash is None:
            self.source_hash = build_source_hash(self.source_memory_ids)
        if self.category is None:
            self.category = "learning"
        if self.x is None:
            self.x = 0.0
        if self.y is None:
            self.y = 0.0
        if self.z is None:
            self.z = 4.0
        if self.gravity is None:
            self.gravity = 0.5
        if self.sync_transaction_id is None:
            self.sync_transaction_id = uuid.uuid4().hex
        if self.is_deleted is None:
            self.is_deleted = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "wisdom_text": self.wisdom_text,
            "style": self.style,
            "source_memory_ids": self.source_memory_ids,
            "source_sync_transaction_ids": self.source_sync_transaction_ids,
            "source_hash": self.source_hash,
            "trigger_type": self.trigger_type,
            "importance": self.importance,
            "category": self.category,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "gravity": self.gravity,
            "embedding": self.embedding,
            "sync_transaction_id": self.sync_transaction_id,
            "is_deleted": self.is_deleted,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
