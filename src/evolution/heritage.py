"""认知基因的脱敏提取与传承。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config.settings import settings
from src.execution import ToolProvisioner
from src.observability import get_action_journal
from src.security import LogShredder
from src.utils.logger import log
from src.world_model import WorldModel


@dataclass(frozen=True)
class HeritageMetadata:
    """克隆包元数据与逻辑签名。"""

    version: str = "1.0.0"
    origin: str = "abu-prime-node-01"
    generated_at: str = ""
    parent_hash: str = ""
    source_trace_id: str = ""
    gene_hash: str = ""
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def sign_package(
        cls,
        package_data: Dict[str, Any],
        *,
        version: str = "1.0.0",
        origin: str = "abu-prime-node-01",
        generated_at: str,
        parent_hash: str,
        source_trace_id: str = "",
    ) -> Dict[str, Any]:
        payload = dict(package_data)
        gene_hash = cls._hash_payload(
            {
                "gene_rules": payload.get("gene_rules", []),
                "gravity_bias": payload.get("gravity_bias", []),
                "tool_allow_list": payload.get("tool_allow_list", []),
            }
        )
        unsigned_metadata = {
            "version": version,
            "origin": origin,
            "generated_at": generated_at,
            "parent_hash": parent_hash,
            "source_trace_id": source_trace_id,
            "gene_hash": gene_hash,
        }
        signature = cls._hash_payload(
            {
                **payload,
                "metadata": unsigned_metadata,
            }
        )
        metadata = cls(
            version=version,
            origin=origin,
            generated_at=generated_at,
            parent_hash=parent_hash,
            source_trace_id=source_trace_id,
            gene_hash=gene_hash,
            signature=signature,
        )
        payload["metadata"] = metadata.to_dict()
        return payload

    @classmethod
    def verify_package(cls, package_data: Dict[str, Any]) -> bool:
        metadata = package_data.get("metadata") or {}
        if not metadata:
            return False
        expected_gene_hash = cls._hash_payload(
            {
                "gene_rules": package_data.get("gene_rules", []),
                "gravity_bias": package_data.get("gravity_bias", []),
                "tool_allow_list": package_data.get("tool_allow_list", []),
            }
        )
        if metadata.get("gene_hash") != expected_gene_hash:
            return False

        unsigned_metadata = {
            "version": metadata.get("version", ""),
            "origin": metadata.get("origin", ""),
            "generated_at": metadata.get("generated_at", ""),
            "parent_hash": metadata.get("parent_hash", ""),
            "source_trace_id": metadata.get("source_trace_id", ""),
            "gene_hash": metadata.get("gene_hash", ""),
        }
        unsigned_payload = {
            key: value
            for key, value in package_data.items()
            if key not in {"metadata", "trace_context", "exchange_context"}
        }
        expected_signature = cls._hash_payload(
            {
                **unsigned_payload,
                "metadata": unsigned_metadata,
            }
        )
        return metadata.get("signature") == expected_signature

    @staticmethod
    def _hash_payload(payload: Dict[str, Any]) -> str:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        return hashlib.sha256(content).hexdigest()


class GeneHeritage:
    """将 ABU 的动态经验转化为静态基因。"""

    MOJIBAKE_MARKERS = ("锟斤拷", "Ã", "æ", "����")

    def __init__(self, memory_manager, world_model: Optional[WorldModel] = None):
        self.memory = memory_manager
        self.world_model = world_model or WorldModel()
        self.shredder = LogShredder()
        self.repo_root = Path(settings.BASE_DIR)
        self.default_gene_path = self.repo_root / "data" / "gene_heritage.json"
        self.default_clone_path = self.repo_root / "data" / "cloning_package.json"
        self.journal = get_action_journal()

    def distill_successful_genes(
        self,
        *,
        output_path: Optional[str] = None,
        injected_rules: Optional[Iterable[str]] = None,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = trace_context or self.journal.reserve_event_context()
        self.journal.log_event(
            component="GeneHeritage",
            stage="gene_distillation",
            action="distill_successful_genes",
            status="started",
            payload={"output_path": output_path or str(self.default_gene_path)},
            priority="normal",
            context=context,
        )
        memories = self.memory.db_manager.list_memories(limit=100000)
        memory_ids = [
            int(item.id) for item in memories if getattr(item, "id", None) is not None
        ]
        rows = self.memory.raw_archive.search_recent_solutions(memory_ids, limit=100)

        rules: List[Dict[str, Any]] = []
        seen_rules = set()
        for row in rows:
            combined = self._combine_row_text(row)
            if not combined or self._should_skip_text(combined):
                continue
            rule_text = self._derive_rule_text(combined)
            if not rule_text or rule_text in seen_rules:
                continue
            seen_rules.add(rule_text)
            rules.append(
                {
                    "rule": rule_text,
                    "source_archive_id": row.get("id"),
                    "source_memory_id": row.get("memory_entry_id"),
                    "verification_status": row.get("verification_status"),
                }
            )

        for item in injected_rules or []:
            sanitized = self.shredder.sanitize_text(str(item or "")).strip()
            if (
                not sanitized
                or self._should_skip_text(sanitized)
                or sanitized in seen_rules
            ):
                continue
            seen_rules.add(sanitized)
            rules.insert(
                0,
                {
                    "rule": sanitized,
                    "source_archive_id": None,
                    "source_memory_id": None,
                    "verification_status": "manual",
                },
            )

        payload = {
            "generated_at": self._now_iso(),
            "gene_rules": rules,
            "rule_count": len(rules),
            "trace_context": context,
        }
        target_path = Path(output_path) if output_path else self.default_gene_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("🧬 基因传承已生成 | rules={} | path={}", len(rules), target_path)
        self.journal.log_event(
            component="GeneHeritage",
            stage="gene_distillation",
            action="distill_successful_genes",
            status="success",
            payload={"rule_count": len(rules), "output_path": str(target_path)},
            priority="normal",
            context=context,
        )
        return payload

    def create_cloning_package(
        self,
        *,
        output_path: Optional[str] = None,
        injected_rules: Optional[Iterable[str]] = None,
        current_image: Optional[str] = None,
        version: str = "1.0.0",
        origin: str = "abu-prime-node-01",
        source_trace_id: str = "",
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = trace_context or self.journal.reserve_event_context(
            trace_id=source_trace_id or None
        )
        self.journal.log_event(
            component="GeneHeritage",
            stage="clone_package",
            action="create_cloning_package",
            status="started",
            payload={
                "current_image": current_image,
                "version": version,
                "origin": origin,
            },
            priority="critical",
            context=context,
        )
        gene_payload = self.distill_successful_genes(
            injected_rules=injected_rules, trace_context=context
        )
        wisdom_entries = self.memory.db_manager.list_semantic_wisdom(limit=15)
        gravity_bias = [
            {
                "id": int(item.id),
                "summary": str(item.wisdom_text or "")[:120],
                "category": item.category,
                "gravity": item.gravity,
                "source_memory_ids": list(item.source_memory_ids or []),
            }
            for item in wisdom_entries
            if getattr(item, "id", None) is not None
        ]

        evolution_map = self._load_evolution_map()
        payload = {
            "generated_at": self._now_iso(),
            "gene_rules": gene_payload.get("gene_rules", []),
            "gravity_bias": gravity_bias,
            "wisdom_node_count": len(evolution_map.get("wisdom_nodes", [])),
            "tool_allow_list": sorted(ToolProvisioner.SAFE_ALLOW_LIST),
            "preferred_sandbox_image": current_image,
            "runtime_hints": {
                "requires_docker": True,
                "sdk_preferred": True,
                "cli_fallback": True,
            },
        }
        parent_hash = self._load_previous_clone_signature()
        signed_payload = HeritageMetadata.sign_package(
            payload,
            version=version,
            origin=origin,
            generated_at=payload["generated_at"],
            parent_hash=parent_hash,
            source_trace_id=context.get("trace_id", ""),
        )
        target_path = Path(output_path) if output_path else self.default_clone_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(signed_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(
            "🧬 克隆包已生成 | rules={} | gravity_bias={} | path={} | signature={}...",
            len(signed_payload["gene_rules"]),
            len(gravity_bias),
            target_path,
            signed_payload["metadata"].get("signature", "")[:12],
        )
        signed_payload["trace_context"] = context
        self.journal.log_event(
            component="GeneHeritage",
            stage="clone_package",
            action="create_cloning_package",
            status="success",
            payload={
                "output_path": str(target_path),
                "signature": signed_payload["metadata"].get("signature", ""),
                "gene_rule_count": len(signed_payload.get("gene_rules", [])),
            },
            priority="critical",
            context=context,
        )
        return signed_payload

    def verify_cloning_package(self, package_data: Dict[str, Any]) -> bool:
        return HeritageMetadata.verify_package(package_data)

    def _combine_row_text(self, row: Dict[str, Any]) -> str:
        combined = " ".join(
            str(row.get(key, "") or "")
            for key in ("raw_event", "raw_thought", "raw_lesson", "full_text")
        ).strip()
        return self.shredder.sanitize_text(combined)

    def _should_skip_text(self, text: str) -> bool:
        lowered = text.lower()
        if "[redacted]" in lowered:
            return True
        if any(marker in text for marker in self.MOJIBAKE_MARKERS):
            return True
        return False

    @staticmethod
    def _derive_rule_text(text: str) -> str:
        lowered = text.lower()
        if all(token in lowered for token in ("pandas", "pip", "success")):
            return "当缺失依赖时，优先使用双通道安装并固化镜像。"
        if any(
            token in lowered
            for token in ("释放缓存", "清理缓存", "reduce concurrency", "降低并发")
        ):
            return "当资源告警出现时，先释放缓存，再降低并发。"
        if any(token in lowered for token in ("信源", "source", "reputation")):
            return "当信息质量不确定时，先做信源判定，再决定是否入脑。"
        return "当出现可复现成功路径时，将解决步骤固化为可迁移规则。"

    def _load_evolution_map(self) -> Dict[str, Any]:
        target = self.repo_root / "evolution_map.json"
        if not target.exists():
            return {}
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_previous_clone_signature(self) -> str:
        if not self.default_clone_path.exists():
            return ""
        try:
            payload = json.loads(self.default_clone_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        metadata = payload.get("metadata") or {}
        return str(metadata.get("signature") or "")

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime

        return datetime.now().isoformat()
