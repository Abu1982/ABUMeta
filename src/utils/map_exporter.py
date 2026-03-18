"""演化地图导出模块"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import numpy as np
import os
import psutil
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
IDIOM_LEXICON_PATH = REPO_ROOT / "src" / "lexicons" / "idiom_anchor_lexicon.json"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import settings


def _maybe_reexec_gpu_venv() -> None:
    current_python = Path(sys.executable).resolve()
    target_python = REPO_ROOT / ".venv-gpu" / "Scripts" / "python.exe"
    if os.environ.get("ABU_GPU_VENV_ACTIVE") == "1":
        return
    if not target_python.exists():
        return
    if current_python == target_python.resolve():
        return
    if sys.version_info >= (3, 14):
        os.environ["ABU_GPU_VENV_ACTIVE"] = "1"
        os.execv(str(target_python), [str(target_python), *sys.argv])


if __name__ == "__main__":
    _maybe_reexec_gpu_venv()

from src.memory import MemoryManager
from src.memory.distiller import MemoryDistiller
from src.memory.models import MemoryEntry
from src.memory.models import SemanticWisdom
from src.perception.sensors import HostMachineSensor, SensorReading
from src.utils.helpers import calculate_similarity, sanitize_text
from src.utils.integrity import IntegrityManager


DEFAULT_OUTPUT_FILENAME = "evolution_map.json"
MAP_SCHEMA_VERSION = "2026-03-15.evolution-map.v2"
_CLUSTER_SIMILARITY_THRESHOLD = 0.70
_WISDOM_CLUSTER_SIMILARITY_THRESHOLD = 0.26
_SOURCE_MATERIAL_SEMANTIC_DEDUP_THRESHOLD = 0.84
_SOURCE_MATERIAL_STABLE_DEDUP_THRESHOLD = 0.91
_STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "及",
    "再",
    "先",
    "后",
    "前",
    "将",
    "把",
    "比",
    "让",
    "在",
    "对",
    "按",
    "用",
    "是",
    "要",
    "会",
    "能",
    "也",
    "并",
    "且",
    "就",
    "才",
    "先",
    "再",
    "去",
    "做",
    "把",
    "会",
    "因",
    "与",
    "中",
    "时",
    "为",
    "或",
    "及",
    "于",
    "等",
    "多条",
    "相近",
    "经验",
    "问题",
    "动作",
    "定位",
    "总结",
    "识别",
    "聚合",
    "学习",
    "模式",
    "经验",
    "教训",
    "知识",
    "智慧",
}
_ANCHOR_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("支付", "重试", "根因"), "溯因止损"),
    (("支付", "失败", "根因"), "溯因止损"),
    (("复盘", "学习", "总结"), "复盘成策"),
    (("复盘", "模式", "瓶颈"), "复盘成策"),
    (("瓶颈", "模式", "总结"), "复盘成策"),
]
_AI_ANCHOR_LEXICON: List[Tuple[Tuple[str, ...], str]] = [
    (("ai编程",), "智编"),
    (("编程", "自动化"), "智编"),
    (("ai", "代码"), "智编"),
    (("交付",), "交付"),
    (("自动化",), "自动化"),
]
_HARDWARE_ANCHOR_LEXICON: List[Tuple[Tuple[str, ...], str]] = [
    (("显存",), "显存"),
    (("扩容",), "扩容"),
    (("存储",), "存储"),
]
_CONNECTOR_TOKENS = {"与", "和", "及"}
_CATEGORY_FALLBACK_PREFIX = {
    "hardware": "硬域",
    "survival": "生域",
    "finance": "财域",
    "learning": "学域",
    "culture": "文域",
}
_SUMMARY_PREFIX = {
    "hardware": "硬件经验聚类",
    "survival": "生存经验聚类",
    "finance": "支付失败经验聚类",
    "learning": "学习复盘经验聚类",
    "culture": "文化经验聚类",
}
_POLAR_RADIUS_BY_Z = {
    1.0: 1.6,
    2.0: 2.5,
    3.0: 3.4,
    4.0: 4.2,
    5.0: 5.0,
}
_ORBITAL_JITTER_TRIGGER_DEGREES = 15.0
_MIN_ORBITAL_ARC_LENGTH = 2.0
_MIN_COORDINATE_MAGNITUDE = 1.0
_CULTURE_ANCHOR_LEXICON: List[Tuple[Tuple[str, ...], str]] = [
    (("格物", "致知"), "格物致知"),
    (("厚积", "薄发"), "厚积薄发"),
    (("经世", "致用"), "经世致用"),
    (("大道", "至简"), "大道至简"),
    (("兼收", "并蓄"), "兼收并蓄"),
    (("开源", "文化"), "兼收并蓄"),
    (("极简", "文化"), "大道至简"),
]
_DISPLAY_TEXT_REPLACEMENTS: List[Tuple[str, str]] = [
    ("认知蒸馏", "认知整理"),
    ("蒸馏去重", "聚合去重"),
    ("接入百炼模型", "接入语义模型"),
    ("百炼 Anthropic 兼容地址", "兼容模型地址"),
    ("百炼", "语义"),
    ("蒸馏", "整理"),
]
_GENERIC_ANCHOR_TOKENS = {
    "项目介绍",
    "新闻事实",
    "摘要",
    "关键实体",
    "因果分析",
    "泛化经验",
    "项目",
    "介绍",
    "事实",
}
_ACTIVITY_PREFIXES = (
    "自主巡航",
    "基因阻断",
    "生产心跳",
    "巡航任务",
    "系统",
    "生产态",
)
_ANCHOR_COMPRESSION_MAP = {
    "交付": "学以致用",
    "文化事实": "格物致知",
    "项目介绍": "知行合一",
    "新闻事实": "格物致知",
    "显存临界": "未雨绸缪",
    "日志见根": "见微知著",
    "先辨信源": "择邻而处",
    "无为重减": "无为而治",
}


def collect_git_timeline(
    repo_root: str, integrity_manager: Optional[IntegrityManager] = None
) -> List[Dict[str, Any]]:
    manager = integrity_manager or IntegrityManager(repo_path=repo_root)
    result = manager.run_git_command(
        [
            "log",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%ad%x1f%s",
            "--",
        ]
    )
    if result.exit_code != 0 or not result.stdout:
        return []

    timeline = []
    for line in result.stdout.splitlines():
        commit_hash, committed_at, message = _split_git_line(line)
        if not commit_hash:
            continue
        timeline.append(
            {
                "commit": commit_hash,
                "committed_at": committed_at,
                "message": message,
            }
        )
    return timeline


def scan_module_edges(src_root: str) -> List[Dict[str, Any]]:
    return scan_code_roots([Path(src_root)])


def scan_code_roots(roots: Sequence[Path]) -> List[Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue

            module_name = _module_name_from_path(path, root)
            symbols = extract_module_symbols(tree)
            imports = extract_module_imports(tree, module_name)
            edges.append(
                {
                    "module": module_name,
                    "file": str(path.relative_to(root.parent)).replace("\\", "/"),
                    "updated_at": _path_mtime_iso(path),
                    "classes": symbols["classes"],
                    "functions": symbols["functions"],
                    "imports": imports,
                }
            )
    return edges


def extract_module_symbols(tree: ast.AST) -> Dict[str, List[str]]:
    classes: List[str] = []
    functions: List[str] = []

    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)

    return {"classes": classes, "functions": functions}


def extract_module_imports(tree: ast.AST, source_module: str) -> List[str]:
    imports: List[str] = []
    seen = set()

    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if _should_include_import(target) and target not in seen:
                    seen.add(target)
                    imports.append(target)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                target = _resolve_relative_module(
                    source_module, node.module or "", node.level
                )
            else:
                target = node.module or ""
            if _should_include_import(target) and target not in seen:
                seen.add(target)
                imports.append(target)

    return sorted(imports)


def collect_wisdom_nodes(memory_manager: MemoryManager) -> List[Dict[str, Any]]:
    wisdom_records = memory_manager.db_manager.list_semantic_wisdom(limit=100000)
    clusters = _cluster_wisdom_records(wisdom_records)
    distiller = MemoryDistiller()
    nodes: List[Dict[str, Any]] = []

    for cluster_records in clusters:
        category = cluster_records[0].category or "learning"
        contains_ids = sorted(
            record.id for record in cluster_records if record.id is not None
        )
        contains = _compress_wisdom_contains(
            [
                {
                    "id": record.id,
                    "summary": _normalize_memory_summary(record.wisdom_text),
                    "created_at": _to_jsonable(record.created_at),
                }
                for record in sorted(
                    cluster_records, key=lambda record: (record.id or 0)
                )
                if record.id is not None
            ]
        )
        importance = _aggregate_cluster_importance(cluster_records)
        z = _resolve_cluster_z(cluster_records, distiller=distiller)
        gravity = distiller.calculate_gravity(
            importance, source_count=len(contains_ids), category=category
        )
        anchor = _compress_wisdom_anchor(
            _generate_anchor(cluster_records), cluster_records
        )
        if _is_activity_anchor(anchor):
            continue
        nodes.append(
            {
                "id": _build_cluster_id(category, contains_ids),
                "anchor": anchor,
                "topic_summary": _generate_topic_summary(
                    cluster_records, anchor=anchor
                ),
                "importance": importance,
                "contains": contains,
                "created_at": _to_jsonable(cluster_records[0].created_at),
                "x": 0.0,
                "y": 0.0,
                "z": z,
                "gravity": gravity,
                "_category": category,
            }
        )

    nodes = _merge_wisdom_nodes_by_anchor(nodes, distiller=distiller)
    re_eval_queue = _load_json_file(
        Path(settings.BASE_DIR) / "data" / "reports" / "wisdom_re_evaluation_queue.json"
    )
    re_eval_ids = {
        int(item) for item in re_eval_queue.get("wisdom_ids", []) if str(item).isdigit()
    }
    _assign_cluster_positions(nodes)
    for node in nodes:
        node.pop("_category", None)
        contains_ids = {
            int(item.get("id"))
            for item in node.get("contains", [])
            if item.get("id") is not None and str(item.get("id")).isdigit()
        }
        if contains_ids & re_eval_ids:
            node["status"] = "re-evaluating"
            node["re_evaluating_at"] = re_eval_queue.get("generated_at")

    return sorted(nodes, key=lambda node: (node["z"], node["id"]))


def _merge_wisdom_nodes_by_anchor(
    nodes: List[Dict[str, Any]],
    distiller: Optional[MemoryDistiller] = None,
) -> List[Dict[str, Any]]:
    helper = distiller or MemoryDistiller()
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        grouped[node["anchor"]].append(node)

    merged: List[Dict[str, Any]] = []
    for anchor, items in grouped.items():
        if len(items) == 1:
            merged.append(items[0])
            continue

        category_buckets: Dict[str, float] = defaultdict(float)
        all_contains: List[Dict[str, Any]] = []
        for item in items:
            category_buckets[item["_category"]] += float(item["gravity"])
            all_contains.extend(item.get("contains", []))

        dominant_category = max(
            category_buckets.items(), key=lambda entry: (entry[1], entry[0])
        )[0]
        contains_by_id = {int(item["id"]): item for item in all_contains}
        contains = _compress_wisdom_contains(
            [contains_by_id[key] for key in sorted(contains_by_id)]
        )
        importance = round(max(float(item["importance"]) for item in items), 6)
        gravity = helper.calculate_gravity(
            importance=importance,
            source_count=len(contains),
            category=dominant_category,
        )
        topic_summary = max(
            items,
            key=lambda item: (float(item["gravity"]), float(item["importance"])),
        )["topic_summary"]
        created_at_values = [
            parsed
            for item in items
            for parsed in [_parse_iso_datetime(item.get("created_at"))]
            if parsed is not None
        ]
        created_at = min(created_at_values).isoformat() if created_at_values else None

        merged.append(
            {
                "id": _build_cluster_id(
                    dominant_category, [int(item["id"]) for item in contains]
                ),
                "anchor": anchor,
                "topic_summary": topic_summary,
                "importance": importance,
                "contains": contains,
                "created_at": created_at,
                "x": 0.0,
                "y": 0.0,
                "z": helper.get_z_for_category(dominant_category),
                "gravity": gravity,
                "_category": dominant_category,
            }
        )

    return merged


def _compress_wisdom_contains(
    contains: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in contains:
        summary = _normalize_memory_summary(str(item.get("summary") or ""))
        if not summary:
            continue
        grouped[summary].append(item)

    compressed: List[Dict[str, Any]] = []
    for summary, items in grouped.items():
        sorted_items = sorted(
            items,
            key=lambda entry: (
                _parse_iso_datetime(entry.get("created_at")) or datetime.max,
                int(entry.get("id") or 0),
            ),
        )
        representative = sorted_items[0]
        compressed.append(
            {
                "id": int(representative.get("id") or 0),
                "summary": summary,
                "created_at": str(representative.get("created_at") or ""),
            }
        )
    return sorted(compressed, key=lambda item: int(item["id"]))


def collect_vector_groups(memory_manager: MemoryManager) -> List[Dict[str, Any]]:
    memory_records = memory_manager.db_manager.list_memories(limit=100000)
    distiller = MemoryDistiller()
    prepared_records = _prepare_memory_records(memory_manager, memory_records)
    clusters = _cluster_memory_records(prepared_records)
    nodes = _build_vector_group_nodes(clusters, distiller=distiller)
    _project_vector_group_positions(nodes)
    for node in nodes:
        node["layer"] = "vector_group"
    return sorted(nodes, key=lambda node: node["id"])


def collect_pattern_cluster_nodes(repo_path: Path) -> List[Dict[str, Any]]:
    log_path = (
        repo_path
        / "data"
        / "inquiries"
        / "processed"
        / "pattern_distillation_log.jsonl"
    )
    if not log_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in rows[-50:]:
        report_id = str(item.get("report_id") or "unknown_report")
        distillation = item.get("distillation", {}) or {}
        category = str(distillation.get("category") or "pattern_cluster")
        trigger_type = str(distillation.get("trigger_type") or "unknown")
        cluster_key = f"{category}:{trigger_type}"
        bucket = grouped.setdefault(
            cluster_key,
            {
                "report_ids": [],
                "wisdom_ids": [],
                "memory_ids": [],
                "candidate_count": 0,
                "gravity": 0.0,
                "z": float(distillation.get("z") or 2.5),
                "created_at": item.get("generated_at"),
            },
        )
        bucket["report_ids"].append(report_id)
        bucket["wisdom_ids"].extend(
            [
                int(value)
                for value in distillation.get("wisdom_ids", [])
                if str(value).isdigit()
            ]
        )
        bucket["memory_ids"].extend(
            [int(value) for value in item.get("memory_ids", []) if str(value).isdigit()]
        )
        bucket["candidate_count"] += int(item.get("candidate_count") or 0)
        bucket["gravity"] = max(
            float(bucket["gravity"]), float(distillation.get("gravity") or 0.85)
        )
        bucket["created_at"] = item.get("generated_at") or bucket["created_at"]

    nodes: List[Dict[str, Any]] = []
    for cluster_key, bucket in grouped.items():
        unique_reports = sorted(set(bucket["report_ids"]))
        unique_wisdom = sorted(set(bucket["wisdom_ids"]))
        unique_memory = sorted(set(bucket["memory_ids"]))
        contains = [
            {
                "id": value,
                "summary": f"memory:{value}",
                "created_at": bucket["created_at"],
            }
            for value in unique_memory
        ]
        nodes.append(
            {
                "id": f"pattern-cluster:{cluster_key}",
                "anchor": f"真实报告模式簇:{cluster_key}",
                "topic_summary": (
                    f"模式簇 {cluster_key} 已覆盖 {len(unique_reports)} 份报告，"
                    f"累计 {bucket['candidate_count']} 条候选，生成 wisdom {unique_wisdom or []}。"
                ),
                "importance": float(bucket["gravity"] or 0.85),
                "contains": contains,
                "x": 0.0,
                "y": 0.0,
                "z": float(bucket["z"] or 2.5),
                "gravity": float(bucket["gravity"] or 0.85),
                "category": "pattern_cluster",
                "layer": "pattern_cluster",
                "report_ids": unique_reports,
                "wisdom_ids": unique_wisdom,
                "created_at": bucket["created_at"],
            }
        )
    return sorted(nodes, key=lambda node: node["id"])


def collect_activity_nodes(memory_manager: MemoryManager) -> List[Dict[str, Any]]:
    memory_records = memory_manager.db_manager.list_memories(limit=100000)
    prepared_records = _prepare_memory_records(memory_manager, memory_records)
    dedup: Dict[tuple[str, str], Dict[str, Any]] = {}
    for record in prepared_records:
        summary = record["summary"]
        if not _is_activity_summary(summary):
            continue
        node = {
            "id": f"activity:{record['id']}",
            "anchor": _activity_anchor(summary),
            "summary": _normalize_memory_summary(summary),
            "created_at": _to_jsonable(record["timestamp"]),
            "category": _infer_activity_category(summary),
        }
        dedup_key = (node["summary"], node["category"])
        existing = dedup.get(dedup_key)
        if existing is None or str(node["created_at"]) > str(existing["created_at"]):
            dedup[dedup_key] = node
    return sorted(dedup.values(), key=lambda node: node["id"])


def collect_source_materials(memory_manager: MemoryManager) -> List[Dict[str, Any]]:
    materials, _ = _build_source_material_payload(memory_manager)
    return materials


def collect_source_materials_dedup(memory_manager: MemoryManager) -> Dict[str, Any]:
    _, report = _build_source_material_payload(memory_manager)
    return report


def collect_knowledge_classification_status(repo_path: Path) -> Dict[str, Any]:
    anchor_registry = _load_json_file(
        repo_path / "data" / "reports" / "pattern_anchor_registry.json"
    )
    source_dedup = _load_json_file(
        repo_path / "data" / "reports" / "source_materials_dedup_report.json"
    )
    source_breakdown = source_dedup.get("dedup", {}).get("source_type_breakdown", {})
    counts = anchor_registry.get("counts", {}) or {}
    return {
        "anchor_registry_status": anchor_registry.get("status", "missing"),
        "anchor_count": len(anchor_registry.get("items", [])),
        "stable_anchor_count": counts.get("stable_anchor", 0),
        "review_pending_count": counts.get("review_pending", 0),
        "deferred_count": counts.get("deferred", 0),
        "source_type_breakdown": source_breakdown,
        "stability_grade": source_dedup.get("dedup", {}).get("stability_grade"),
        "dominant_cluster_ratio": source_dedup.get("dedup", {}).get(
            "dominant_cluster_ratio"
        ),
        "path_anchor_registry": "data/reports/pattern_anchor_registry.json",
        "path_source_dedup": "data/reports/source_materials_dedup_report.json",
    }


def _build_source_material_payload(
    memory_manager: MemoryManager,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    memory_records = memory_manager.db_manager.list_memories(limit=100000)
    prepared_records = _prepare_memory_records(memory_manager, memory_records)
    candidates: List[Dict[str, Any]] = []
    for record in prepared_records:
        summary = record["summary"]
        if _is_activity_summary(summary):
            continue
        if _looks_like_condensed_wisdom(summary):
            continue
        normalized_summary = _normalize_memory_summary(summary)
        candidates.append(
            {
                "id": int(record["id"]),
                "summary": normalized_summary,
                "created_at": _to_jsonable(record["timestamp"]),
                "importance": float(record["importance"]),
                "source_type": _infer_source_type(summary),
                "embedding": record.get("embedding", []),
                "signature": _source_material_signature(normalized_summary),
            }
        )

    clusters = _cluster_source_material_records(candidates)
    materials: List[Dict[str, Any]] = []
    duplicate_clusters: List[Dict[str, Any]] = []
    duplicates_removed = 0
    for index, cluster in enumerate(clusters, start=1):
        representative = sorted(
            cluster,
            key=lambda item: (
                -float(item["importance"]),
                str(item["created_at"]),
                int(item["id"]),
            ),
            reverse=False,
        )[0]
        merged_ids = [
            int(item["id"])
            for item in sorted(cluster, key=lambda item: int(item["id"]))
        ]
        duplicate_count = max(0, len(cluster) - 1)
        materials.append(
            {
                "id": f"source:{representative['id']}",
                "summary": representative["summary"],
                "created_at": representative["created_at"],
                "importance": representative["importance"],
                "source_type": representative["source_type"],
                "duplicate_count": duplicate_count,
                "merged_memory_ids": merged_ids,
                "dedup_signature": representative["signature"],
            }
        )
        if duplicate_count > 0:
            duplicates_removed += duplicate_count
            duplicate_clusters.append(
                {
                    "cluster_id": f"source-material:{index}",
                    "source_type": representative["source_type"],
                    "representative_id": representative["id"],
                    "representative_summary": representative["summary"],
                    "duplicate_count": duplicate_count,
                    "merged_memory_ids": merged_ids,
                    "earliest_created_at": min(
                        str(item["created_at"]) for item in cluster
                    ),
                    "latest_created_at": max(
                        str(item["created_at"]) for item in cluster
                    ),
                }
            )
    source_type_breakdown: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"original_count": 0, "deduped_count": 0, "duplicates_removed": 0}
    )
    for item in candidates:
        source_type_breakdown[item["source_type"]]["original_count"] += 1
    for item in materials:
        source_type_breakdown[item["source_type"]]["deduped_count"] += 1
        source_type_breakdown[item["source_type"]]["duplicates_removed"] += int(
            item.get("duplicate_count") or 0
        )
    largest_cluster = max((len(cluster) for cluster in clusters), default=0)
    compression_ratio = (
        round(duplicates_removed / max(len(candidates), 1), 6) if candidates else 0.0
    )
    dominant_cluster_ratio = (
        round(largest_cluster / max(len(candidates), 1), 6) if candidates else 0.0
    )
    compact_material_cap = max(8, len(source_type_breakdown) * 3)
    stability_grade = (
        "stable"
        if len(candidates) <= 1
        else "compressed_stable"
        if dominant_cluster_ratio >= 0.45
        and compression_ratio >= 0.8
        and len(materials) <= compact_material_cap
        else "volatile"
        if dominant_cluster_ratio >= 0.45
        else "watch"
        if dominant_cluster_ratio >= 0.25 or compression_ratio >= 0.65
        else "stable"
    )
    report = {
        "status": "deduped" if duplicates_removed else "clean",
        "original_count": len(candidates),
        "deduped_count": len(materials),
        "duplicates_removed": duplicates_removed,
        "duplicate_cluster_count": len(duplicate_clusters),
        "compression_ratio": compression_ratio,
        "largest_cluster_size": largest_cluster,
        "dominant_cluster_ratio": dominant_cluster_ratio,
        "stability_grade": stability_grade,
        "source_type_breakdown": dict(source_type_breakdown),
        "compressed_history_preview": duplicate_clusters[:10],
        "clusters": duplicate_clusters[:20],
    }
    return sorted(materials, key=lambda item: item["id"]), report


def _cluster_source_material_records(
    records: Sequence[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    if not records:
        return []
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[str(record.get("source_type") or "learning_source")].append(record)
    clustered: List[List[Dict[str, Any]]] = []
    for source_type in sorted(buckets):
        source_records = sorted(
            buckets[source_type],
            key=lambda item: (
                str(item.get("created_at") or ""),
                int(item.get("id") or 0),
            ),
        )
        adjacency: Dict[int, set[int]] = {
            index: set() for index in range(len(source_records))
        }
        for left_index, left_record in enumerate(source_records):
            for right_index in range(left_index + 1, len(source_records)):
                right_record = source_records[right_index]
                if (
                    _source_material_similarity(left_record, right_record)
                    >= _SOURCE_MATERIAL_SEMANTIC_DEDUP_THRESHOLD
                ):
                    adjacency[left_index].add(right_index)
                    adjacency[right_index].add(left_index)
        visited = set()
        for index in range(len(source_records)):
            if index in visited:
                continue
            stack = [index]
            component: List[Dict[str, Any]] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(source_records[current])
                stack.extend(sorted(adjacency[current] - visited, reverse=True))
            clustered.append(
                sorted(
                    component,
                    key=lambda item: (-float(item["importance"]), int(item["id"])),
                )
            )
    return clustered


def _source_material_similarity(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    if left.get("source_type") != right.get("source_type"):
        return 0.0
    if left.get("signature") and left.get("signature") == right.get("signature"):
        return 1.0
    text_similarity = calculate_similarity(
        str(left.get("summary") or ""), str(right.get("summary") or "")
    )
    left_tokens = set(_extract_topic_tokens(str(left.get("summary") or "")))
    right_tokens = set(_extract_topic_tokens(str(right.get("summary") or "")))
    token_overlap = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if (left_tokens or right_tokens)
        else 0.0
    )
    embedding_similarity = _embedding_cosine_similarity(
        left.get("embedding", []), right.get("embedding", [])
    )
    if (
        text_similarity >= _SOURCE_MATERIAL_STABLE_DEDUP_THRESHOLD
        and token_overlap >= 0.6
    ):
        return 0.95
    return round(
        0.4 * embedding_similarity + 0.4 * text_similarity + 0.2 * token_overlap,
        6,
    )


def _source_material_signature(text: str) -> str:
    normalized = sanitize_text(text or "").lower()
    normalized = re.sub(r"[\s\W_]+", "", normalized)
    return normalized[:64]


def _prepare_memory_records(
    memory_manager: MemoryManager,
    memory_records: Sequence[MemoryEntry],
) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    for memory in memory_records:
        memory_id = int(memory.id)
        summary = _memory_summary(memory)
        embedding = [float(value) for value in (memory.embedding or [])]
        if not embedding:
            embedding = memory_manager.vector_retriever.generate_embedding(summary)
            memory_manager.db_manager.update_memory(memory_id, embedding=embedding)
        prepared.append(
            {
                "id": memory_id,
                "event": str(memory.event or ""),
                "thought": str(memory.thought or ""),
                "lesson": str(memory.lesson or ""),
                "importance": float(memory.importance or 0.0),
                "timestamp": memory.timestamp,
                "summary": summary,
                "embedding": embedding,
            }
        )
    return prepared


def _memory_summary(memory: MemoryEntry) -> str:
    parts = [str(memory.event or "").strip()]
    if memory.lesson:
        parts.append(str(memory.lesson).strip())
    elif memory.thought:
        parts.append(str(memory.thought).strip())
    text = "；".join(part for part in parts if part)
    return _normalize_memory_summary(text) or f"记忆{int(memory.id)}"


def _normalize_memory_summary(text: str) -> str:
    sanitized = sanitize_text(text or "")
    sanitized = re.sub(r"^新闻事实[:：]\s*", "", sanitized)
    sanitized = re.sub(r"\|\s*关键实体[:：].*$", "", sanitized)
    sanitized = re.sub(r"摘要[:：]", "", sanitized)
    sanitized = re.sub(r"泛化经验[:：]", "经验：", sanitized)
    sanitized = re.sub(r"因果分析[:：]", "分析：", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > 72:
        sanitized = sanitized[:71].rstrip("，；。,. ") + "。"
    elif sanitized and sanitized[-1] not in "。；!?！？":
        sanitized += "。"
    return sanitized


def _looks_like_condensed_wisdom(text: str) -> bool:
    lowered = sanitize_text(text or "")
    if len(lowered) > 80:
        return False
    return (
        ("经验：" in lowered)
        or ("；" in lowered)
        or ("。" in lowered and len(lowered) <= 40)
    )


def _is_activity_summary(text: str) -> bool:
    normalized = sanitize_text(text or "")
    return any(normalized.startswith(prefix) for prefix in _ACTIVITY_PREFIXES)


def _activity_anchor(text: str) -> str:
    normalized = sanitize_text(text or "")
    for prefix in _ACTIVITY_PREFIXES:
        if normalized.startswith(prefix):
            return prefix
    return "活动记录"


def _infer_activity_category(text: str) -> str:
    normalized = sanitize_text(text or "")
    if "交易" in normalized or "现金" in normalized or "账本" in normalized:
        return "finance"
    if "无为" in normalized or "择邻" in normalized:
        return "culture"
    return "system"


def _infer_source_type(text: str) -> str:
    normalized = sanitize_text(text or "")
    if normalized.startswith("GitHub Trending"):
        return "github_trending"
    if "财联社" in normalized or "公募" in normalized or "投资机会" in normalized:
        return "finance_news"
    if "文化" in normalized or "经典" in normalized:
        return "culture_source"
    return "learning_source"


def _cluster_memory_records(
    records: Sequence[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    if not records:
        return []
    adjacency: Dict[int, set[int]] = {index: set() for index in range(len(records))}
    for left_index, left_record in enumerate(records):
        for right_index in range(left_index + 1, len(records)):
            right_record = records[right_index]
            if (
                _embedding_cosine_similarity(
                    left_record["embedding"], right_record["embedding"]
                )
                >= _CLUSTER_SIMILARITY_THRESHOLD
            ):
                adjacency[left_index].add(right_index)
                adjacency[right_index].add(left_index)

    visited = set()
    clustered: List[List[Dict[str, Any]]] = []
    for index in range(len(records)):
        if index in visited:
            continue
        stack = [index]
        component: List[Dict[str, Any]] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(records[current])
            stack.extend(sorted(adjacency[current] - visited, reverse=True))
        clustered.append(
            sorted(component, key=lambda item: (-item["importance"], item["id"]))
        )
    return clustered


def _embedding_cosine_similarity(
    left: Sequence[float], right: Sequence[float]
) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_vector = np.asarray(left, dtype=float)
    right_vector = np.asarray(right, dtype=float)
    numerator = float(np.dot(left_vector, right_vector))
    denominator = float(np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
    if denominator == 0.0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _build_vector_group_nodes(
    clusters: Sequence[Sequence[Dict[str, Any]]],
    distiller: Optional[MemoryDistiller] = None,
) -> List[Dict[str, Any]]:
    helper = distiller or MemoryDistiller()
    nodes: List[Dict[str, Any]] = []
    for cluster in clusters:
        contains_ids = [int(item["id"]) for item in cluster]
        cluster_text = " ".join(item["summary"] for item in cluster)
        category = helper.infer_category(cluster_text)
        contains = [
            {
                "id": int(item["id"]),
                "summary": item["summary"],
                "created_at": _to_jsonable(item["timestamp"]),
            }
            for item in sorted(cluster, key=lambda item: item["id"])
        ]
        centroid = _mean_normalized_vector(
            [item["embedding"] for item in cluster if item["embedding"]]
        )
        importance = round(
            sum(item["importance"] for item in cluster) / max(len(cluster), 1), 6
        )
        gravity = helper.calculate_gravity(
            importance=importance,
            source_count=len(contains_ids),
            category=category,
        )
        anchor = _generate_memory_cluster_anchor(cluster, category)
        nodes.append(
            {
                "id": _build_cluster_id(category, contains_ids),
                "anchor": anchor,
                "topic_summary": _generate_memory_cluster_summary(
                    cluster, category, anchor
                ),
                "importance": importance,
                "contains": contains,
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "gravity": gravity,
                "category": category,
                "embedding": centroid,
            }
        )
    return nodes


def _mean_normalized_vector(vectors: Sequence[Sequence[float]]) -> List[float]:
    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=float)
    centroid = np.mean(matrix, axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm == 0.0:
        return centroid.tolist()
    return (centroid / norm).tolist()


def _compress_wisdom_anchor(
    anchor: str,
    cluster_records: Sequence[SemanticWisdom],
) -> str:
    normalized = sanitize_text(anchor or "").strip()
    cluster_text = " ".join(record.wisdom_text or "" for record in cluster_records)
    category = cluster_records[0].category if cluster_records else None
    idiom_anchor = _match_idiom_anchor_lexicon(cluster_text, category=category)
    normalized = idiom_anchor or _canonicalize_anchor(cluster_text, normalized)
    if normalized in _ANCHOR_COMPRESSION_MAP:
        normalized = _ANCHOR_COMPRESSION_MAP[normalized]
    if normalized in _GENERIC_ANCHOR_TOKENS or _is_activity_anchor(normalized):
        lead = cluster_records[0].wisdom_text if cluster_records else ""
        fallback = _extract_lead_anchor_keyword(lead) or "知行合一"
        normalized = fallback
    if len(normalized) < 4:
        normalized = _expand_short_anchor(normalized, cluster_records)
    if len(normalized) > 16:
        normalized = normalized[:16]
    return normalized or "知行合一"


def _canonicalize_anchor(cluster_text: str, anchor: str) -> str:
    normalized_text = sanitize_text(cluster_text or "")
    if "知足" in normalized_text:
        return "知足不辱"
    if "无为" in normalized_text:
        return "无为而治"
    if (
        "择邻" in normalized_text
        or "信源" in normalized_text
        or "高信" in normalized_text
    ):
        return "择邻而处"
    if "日志" in normalized_text or "根因" in normalized_text:
        return "见微知著"
    if (
        "交付" in normalized_text
        or "结果" in normalized_text
        or "输出" in normalized_text
    ):
        return "学以致用"
    if (
        "交易" in normalized_text
        or "止损" in normalized_text
        or "现金" in normalized_text
    ):
        return "谋定后动"
    return anchor


@lru_cache(maxsize=1)
def _load_idiom_anchor_lexicon() -> List[Dict[str, Any]]:
    if not IDIOM_LEXICON_PATH.exists():
        return []
    payload = json.loads(IDIOM_LEXICON_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("anchor")]


def _match_idiom_anchor_lexicon(
    cluster_text: str,
    category: Optional[str] = None,
) -> Optional[str]:
    token_set = set(_extract_topic_tokens(cluster_text))
    if not token_set:
        return None

    best_anchor: Optional[str] = None
    best_score = 0.0
    best_priority = -1
    for entry in _load_idiom_anchor_lexicon():
        entry_category = entry.get("category")
        if category and entry_category and entry_category not in {category, "any"}:
            continue
        keywords = [
            str(keyword).strip().lower()
            for keyword in entry.get("keywords", [])
            if str(keyword).strip()
        ]
        if not keywords:
            continue
        matched = sum(1 for keyword in keywords if keyword in token_set)
        if matched == 0:
            continue
        score = matched / len(keywords)
        priority = int(entry.get("priority", 0) or 0)
        if score > best_score or (score == best_score and priority > best_priority):
            best_score = score
            best_priority = priority
            best_anchor = str(entry["anchor"])

    if best_score < 0.2:
        return None
    return best_anchor


def _is_activity_anchor(anchor: str) -> bool:
    normalized = sanitize_text(anchor or "")
    return normalized in {"自主巡航", "基因阻断", "活动记录", "系统"}


def _expand_short_anchor(
    anchor: str,
    cluster_records: Sequence[SemanticWisdom],
) -> str:
    combined = " ".join(record.wisdom_text or "" for record in cluster_records)
    lowered = sanitize_text(combined)
    if "交付" in lowered or "输出" in lowered or "结果" in lowered:
        return "学以致用"
    if "文化" in lowered or "文明" in lowered or "经典" in lowered:
        return "格物致知"
    if "风险" in lowered or "止损" in lowered:
        return "知止有度"
    return anchor or "知行合一"


def _generate_memory_cluster_anchor(
    cluster_records: Sequence[Dict[str, Any]],
    category: str,
) -> str:
    repo_anchor = _extract_repo_anchor(cluster_records)
    if repo_anchor is not None:
        return repo_anchor

    token_counter = Counter(
        _extract_topic_tokens(" ".join(item["summary"] for item in cluster_records))
    )
    token_set = set(token_counter)

    if category == "culture":
        culture_anchor = _match_culture_anchor(token_set)
        if culture_anchor is not None:
            return culture_anchor

    for keywords, anchor in _ANCHOR_RULES:
        if all(keyword in token_set for keyword in keywords):
            return anchor

    lexicon_anchor = _match_anchor_lexicon(category, token_set)
    if lexicon_anchor is not None:
        return lexicon_anchor

    lead_text = cluster_records[0]["summary"] if cluster_records else ""
    lead_anchor = _extract_lead_anchor_keyword(lead_text)
    if lead_anchor is not None:
        return lead_anchor

    long_tokens = [
        token
        for token, _ in token_counter.most_common()
        if _is_semantic_anchor_token(token) and token not in _GENERIC_ANCHOR_TOKENS
    ]
    if long_tokens:
        return _normalize_anchor_token(long_tokens[0])

    prefix = _CATEGORY_FALLBACK_PREFIX.get(category, "向量组")
    seed = str(cluster_records[0]["id"] if cluster_records else 0).zfill(2)
    return f"{prefix}{seed[-2:]}"


def _extract_repo_anchor(
    cluster_records: Sequence[Dict[str, Any]],
) -> Optional[str]:
    for item in cluster_records:
        summary = item.get("summary", "") or ""
        match = re.search(
            r"GitHub Trending:\s*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", summary
        )
        if not match:
            continue
        repo_name = match.group(2).strip()
        if repo_name:
            return repo_name[:24]
    return None


def _generate_memory_cluster_summary(
    cluster_records: Sequence[Dict[str, Any]],
    category: str,
    anchor: str,
) -> str:
    lead = max(cluster_records, key=lambda item: (item["importance"], -item["id"]))
    prefix = _SUMMARY_PREFIX.get(category, "向量经验聚类")
    lead_text = _normalize_summary_text(lead["summary"])
    return f"{prefix}：以“{anchor}”为语义中心，聚合了{len(cluster_records)}条向量相近记忆，核心样本是{lead_text}"


def _project_vector_group_positions(nodes: List[Dict[str, Any]]) -> None:
    if not nodes:
        return
    vectors = [node.get("embedding") or [] for node in nodes]
    if not vectors or not vectors[0]:
        return
    matrix = np.asarray(vectors, dtype=float)
    centered = matrix - np.mean(matrix, axis=0, keepdims=True)

    if centered.shape[0] >= 3:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        basis = vh[:3]
        coords = centered @ basis.T
    else:
        coords = np.zeros((centered.shape[0], 3), dtype=float)
        width = centered.shape[1] // 3 or 1
        coords[:, 0] = np.mean(centered[:, :width], axis=1)
        coords[:, 1] = np.mean(centered[:, width : width * 2], axis=1)
        coords[:, 2] = np.mean(centered[:, width * 2 :], axis=1)

    max_abs = np.max(np.abs(coords), axis=0)
    max_abs[max_abs == 0] = 1.0
    scaled = (coords / max_abs) * 5.0
    for index, node in enumerate(nodes):
        node["x"] = round(float(scaled[index, 0]), 6)
        node["y"] = round(float(scaled[index, 1]), 6)
        node["z"] = round(float(scaled[index, 2]), 6)
        node.pop("embedding", None)


def collect_brain_state(brain: Any) -> Optional[Dict[str, Any]]:
    if brain is None:
        return None

    psychological = None
    if getattr(brain, "psyche", None) is not None:
        psychological = dict(brain.psyche.get_current_state())
        psychological["summary"] = brain.psyche.get_psychological_summary()

    goal_focus = None
    if getattr(brain, "goal_focus", None) is not None:
        goal_focus = _to_jsonable(brain.goal_focus.export_snapshot())

    hardware = collect_hardware_state(brain)

    agent_state = None
    if getattr(brain, "state", None) is not None:
        agent_state = _to_jsonable(brain.state)

    return {
        "psychological": psychological,
        "goal_focus": goal_focus,
        "hardware": hardware,
        "agent_state": agent_state,
    }


def collect_hardware_state(brain: Any) -> Dict[str, Any]:
    sensor = None
    perception = getattr(brain, "perception", None)
    if perception is not None:
        sensor = getattr(perception, "registered_sensors", {}).get("host_machine")
    if sensor is None:
        sensor = HostMachineSensor()

    reading = sensor.read() if hasattr(sensor, "read") else HostMachineSensor().read()
    return _sensor_reading_to_payload(reading)


def collect_map_contract() -> Dict[str, Any]:
    return {
        "contract_name": "abu_evolution_map",
        "front_end_compatibility": "stable",
        "required_top_level_keys": [
            "schema_version",
            "generated_at",
            "timeline",
            "module_edges",
            "module_inventory",
            "wisdom_nodes",
            "vector_groups",
            "activity_nodes",
            "source_materials",
            "view_config",
            "module_progress",
            "external_risk_sources",
            "manifesto_candidates",
            "manifesto_draft_snapshot",
            "manifesto_review",
            "runtime_timeline",
            "issue_ledger",
            "runtime_status",
            "report_registry",
            "brain_state",
            "update_protocol",
        ],
        "stable_layer_order": [
            "wisdom_nodes",
            "vector_groups",
            "activity_nodes",
            "source_materials",
        ],
        "append_only_sections": [
            "timeline",
            "activity_nodes",
            "source_materials",
            "report_registry.history",
        ],
        "compatibility_rules": [
            "禁止删除 wisdom_nodes/vector_groups/activity_nodes/source_materials 这四个顶层数组",
            "新增节点必须保留 id、anchor/summary、created_at、category 或 source_type 等基础字段",
            "顶层新增信息优先追加新字段，不重命名既有字段",
            "前端默认展示层继续由 view_config 控制",
        ],
    }


def collect_module_progress() -> List[Dict[str, Any]]:
    return [
        {
            "module_id": "M1",
            "module_name": "基础框架",
            "status": "completed",
            "confidence": "high",
            "evidence": ["docs/04-开发日志/阶段1-完成报告.md"],
            "summary": "项目骨架、配置、日志与主循环基础已落地。",
        },
        {
            "module_id": "M2",
            "module_name": "记忆系统",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "docs/04-开发日志/2026-03-12-阶段2记忆系统实现.md",
                "docs/2026-03-14修复任务.MD",
            ],
            "summary": "记忆、冷库、回查、可信度分层与回溯校验已闭环。",
        },
        {
            "module_id": "M3",
            "module_name": "心理引擎",
            "status": "completed",
            "confidence": "high",
            "evidence": ["docs/04-开发日志/2026-03-12-阶段3心理引擎实现.md"],
            "summary": "情绪、焦虑与行为参数调节已接入主链路。",
        },
        {
            "module_id": "M4",
            "module_name": "金库系统",
            "status": "completed",
            "confidence": "medium",
            "evidence": ["docs/04-开发日志/2026-03-12-阶段4金库系统实现.md"],
            "summary": "资金管理与熔断已完成，阈值规则存在历史迭代。",
        },
        {
            "module_id": "M5",
            "module_name": "时间系统",
            "status": "completed",
            "confidence": "high",
            "evidence": ["docs/04-开发日志/2026-03-12-阶段5时间系统实现.md"],
            "summary": "Chronos 调度、睡眠与后台任务能力已存在。",
        },
        {
            "module_id": "M6",
            "module_name": "语言掩码",
            "status": "completed",
            "confidence": "high",
            "evidence": ["docs/04-开发日志/2026-03-13-阶段6语言掩码实现.md"],
            "summary": "违禁词删除与焦虑驱动风格层已接线。",
        },
        {
            "module_id": "M7",
            "module_name": "世界模型",
            "status": "completed",
            "confidence": "high",
            "evidence": ["docs/04-开发日志/2026-03-13-阶段7世界模型实现.md"],
            "summary": "因果推演与执行前 gate 已接入。",
        },
        {
            "module_id": "M8",
            "module_name": "目标管理与自愈治理",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "docs/04-开发日志/2026-03-13-阶段8目标管理与专注实现.md",
                "src/execution/sandbox.py",
                "src/observability/action_journal.py",
                "src/main_production.py",
                "scripts/manage_production_runtime.py",
            ],
            "summary": "专注系统、自愈治理闭环、目录压力治理与恢复链已完成收口。",
        },
        {
            "module_id": "M9",
            "module_name": "学习系统与外部集成",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "docs/04-开发日志/2026-03-13-阶段9学习能力集成.md",
                "docs/05-测试报告/2026-03-13-学习能力单元测试.md",
                "data/reports/runtime_external_risk_audit.json",
            ],
            "summary": "抓取、解析、外部风险冲突裁决、高层摘要与零事件审计表达均已制度化。",
        },
        {
            "module_id": "M10",
            "module_name": "决策引擎",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "docs/04-开发日志/2026-03-14-阶段10-决策引擎实现.md",
                "data/reports/real_data_trade_leads_enriched_20260315_0230_report.json",
            ],
            "summary": "3-Agent 协调、合规单项溢出与实战分诊已验证。",
        },
        {
            "module_id": "M11",
            "module_name": "自主演化与知识蒸馏",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "docs/04-开发日志/2026-03-14-阶段11-自主巡航实现.md",
                "decision_manifesto.md",
                "data/reports/pattern_anchor_registry.json",
            ],
            "summary": "锚点注册表已活化，稳定锚点、待复核与暂不晋升分层已进入可观测稳态。",
        },
        {
            "module_id": "M12",
            "module_name": "物理工具与抓取执行",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "src/execution/lead_capture.py",
                "src/execution/sandbox.py",
                "data/inquiries/trade_leads_enriched_20260315_0230.csv",
            ],
            "summary": "真实站点抓取、富字段输出与沙盒执行已跑通。",
        },
        {
            "module_id": "M13",
            "module_name": "影子沙盒与隔离验证",
            "status": "completed",
            "confidence": "high",
            "evidence": ["src/execution/sandbox.py"],
            "summary": "Docker 影子沙盒、网络控制与执行验证已落地。",
        },
        {
            "module_id": "M14",
            "module_name": "自主生命循环",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "src/main_production.py",
                ".abu.heartbeat",
                "scripts/manage_production_runtime.py",
            ],
            "summary": "heartbeat、stale/not_running 判定、运行治理与日志轮转已形成长期运维闭环。",
        },
        {
            "module_id": "M15",
            "module_name": "宣言动态演化",
            "status": "completed",
            "confidence": "high",
            "evidence": [
                "decision_manifesto.md",
                "data/reports/decision_manifesto_writeback_policy.json",
            ],
            "summary": "已形成 drill_only 制度结论；正式写回默认受策略阻断，并保留未来升级路径。",
        },
    ]


def collect_runtime_status(repo_path: Path) -> Dict[str, Any]:
    heartbeat = _load_json_file(repo_path / ".abu.heartbeat")
    pattern_distillation = heartbeat.get("pattern_distillation") or _load_json_file(
        repo_path
        / "data"
        / "inquiries"
        / "processed"
        / "latest_pattern_distillation.json"
    )
    pattern_promotion = heartbeat.get("pattern_promotion") or _load_json_file(
        repo_path / "data" / "inquiries" / "processed" / "latest_pattern_promotion.json"
    )
    pattern_batch_report = _load_json_file(
        repo_path / "data" / "reports" / "pattern_distillation_batch_report.json"
    )
    external_risk_event = _load_json_file(
        repo_path / "data" / "reports" / "external_risk_hit_event.json"
    )
    runtime_health_event = _load_json_file(
        repo_path / "data" / "reports" / "runtime_health_event.json"
    )
    runtime_governance_event = _load_json_file(
        repo_path / "data" / "reports" / "runtime_governance_event.json"
    )
    runtime_external_risk_audit = _load_json_file(
        repo_path / "data" / "reports" / "runtime_external_risk_audit.json"
    )
    runtime_timeline = _load_json_file(
        repo_path / "data" / "reports" / "runtime_timeline.json"
    )
    runtime_health = heartbeat.get("runtime_health", {}) or {}
    if not runtime_health or runtime_health.get("long_run_state") is None:
        stable_seconds = int(heartbeat.get("stable_running_seconds") or 0)
        runtime_health = {
            "status": "healthy" if heartbeat.get("phase") == "running" else "watch",
            "stable_running_seconds": stable_seconds,
            "long_run_state": (
                "stable"
                if stable_seconds >= 900
                else "warming"
                if stable_seconds > 0
                else "bootstrap"
            ),
        }
    recovery = heartbeat.get("last_recovery", {}) or {}
    recovery_status_detail = runtime_health.get("recovery_status_detail", {}) or {}
    root_pid = heartbeat.get("process_tree", {}).get("root_pid")
    pid_alive = bool(root_pid) and psutil.pid_exists(int(root_pid))
    heartbeat_timestamp = str(
        heartbeat.get("timestamp") or heartbeat.get("generated_at") or ""
    )
    heartbeat_age_seconds = None
    heartbeat_stale = False
    if heartbeat_timestamp:
        try:
            heartbeat_age_seconds = max(
                0,
                int(
                    (
                        datetime.now() - datetime.fromisoformat(heartbeat_timestamp)
                    ).total_seconds()
                ),
            )
        except ValueError:
            heartbeat_age_seconds = None
    if heartbeat_age_seconds is not None:
        heartbeat_stale = (
            heartbeat_age_seconds > int(heartbeat.get("heartbeat_seconds") or 300) * 2
        )
    runtime_availability = (
        "running"
        if pid_alive and not heartbeat_stale and heartbeat.get("phase") == "running"
        else "not_running"
        if not pid_alive
        else "stale"
        if heartbeat_stale
        else "unknown"
    )
    return {
        "lifecycle": heartbeat.get("lifecycle", {}),
        "heartbeat": {
            "phase": heartbeat.get("phase"),
            "timestamp": heartbeat.get("timestamp"),
            "heartbeat_count": heartbeat.get("heartbeat_count"),
            "heartbeat_seconds": heartbeat.get("heartbeat_seconds"),
        },
        "runtime_availability": runtime_availability,
        "heartbeat_root_pid": root_pid,
        "heartbeat_pid_alive": pid_alive,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_stale": heartbeat_stale,
        "recovery": recovery,
        "shadow_sandbox": heartbeat.get("shadow_sandbox", {}),
        "action_journal": heartbeat.get("action_journal", {}),
        "pattern_distillation": pattern_distillation,
        "pattern_promotion": pattern_promotion,
        "heartbeat_outputs": heartbeat.get("heartbeat_outputs", {}),
        "cruise_output_panel": {
            "heartbeat_phase": heartbeat.get("phase"),
            "heartbeat_count": heartbeat.get("heartbeat_count"),
            "latest_gate_report_id": heartbeat.get("gate_report", {}).get("report_id"),
            "latest_real_report_id": heartbeat.get("real_data_report", {}).get(
                "report_id"
            ),
            "latest_pattern_report_id": pattern_distillation.get("report_id"),
            "latest_pattern_promotion_count": len(
                pattern_promotion.get("generated", [])
            ),
            "latest_batch_count": pattern_batch_report.get("batch_count"),
            "latest_batch_generated_count": pattern_batch_report.get("generated_count"),
            "external_risk_hit_count": external_risk_event.get("hit_count"),
        },
        "external_risk_hit_event": external_risk_event,
        "runtime_health_event": runtime_health_event,
        "runtime_governance_event": runtime_governance_event,
        "runtime_external_risk_audit": runtime_external_risk_audit,
        "runtime_governance_status": runtime_governance_event.get("status", "idle"),
        "runtime_health": runtime_health,
        "recovery_failure_class": recovery_status_detail.get("failure_class")
        or recovery.get("failure_class"),
        "recovery_tier": recovery_status_detail.get("recovery_tier")
        or recovery.get("recovery_tier"),
        "recovery_degrade_reason": recovery_status_detail.get("degrade_reason")
        or recovery.get("degrade_reason"),
        "stable_phase": heartbeat.get("lifecycle", {}).get("stable_phase"),
        "stable_running_seconds": heartbeat.get("stable_running_seconds"),
        "runtime_timeline_event_count": len(runtime_timeline.get("events", [])),
        "update_history": collect_update_history(repo_path, heartbeat),
        "current_focus": "industrial_autonomy_bootstrap",
        "status_summary": "系统已进入 M14/M8 启动期：沙盒 TTL、recovering 状态和 journal 轮转已落地，后续需补恢复动作闭环。",
    }


def collect_external_risk_sources(repo_path: Path) -> Dict[str, Any]:
    catalog = _load_json_file(repo_path / "config" / "external_risk_catalog.json")
    catalogs = []
    for path in sorted((repo_path / "config").glob("external_risk_catalog*.json")):
        payload = _load_json_file(path)
        if payload:
            catalogs.append({"file": path.name, "payload": payload})
    runtime_cache = _load_json_file(
        repo_path / "data" / "cache" / "external_risk_runtime_cache.json"
    )
    sources = [
        source
        for item in catalogs
        for source in item["payload"].get("sources", []) or []
    ]
    cache_sources = runtime_cache.get("sources", []) or []
    return {
        "catalog_version": catalog.get("version"),
        "catalog_count": len(catalogs),
        "catalog_files": [item["file"] for item in catalogs],
        "source_count": len(sources),
        "rule_count": sum(len(source.get("rules", []) or []) for source in sources),
        "runtime_cache_status": runtime_cache.get("status"),
        "runtime_cache_generated_at": runtime_cache.get("generated_at"),
        "runtime_cache_source_count": len(cache_sources),
        "runtime_cache_remote_status": runtime_cache.get("remote_status", []),
        "runtime_cache_remote_summary": runtime_cache.get("remote_summary", {}),
        "runtime_cache_source_priority": runtime_cache.get("source_priority", {}),
        "sources": [
            {
                "source_id": source.get("source_id"),
                "source_type": source.get("source_type"),
                "provider": source.get("provider"),
                "title": source.get("title"),
                "confidence": source.get("confidence"),
                "priority": source.get("priority"),
                "rule_count": len(source.get("rules", []) or []),
                "source_mode": source.get("source_mode"),
                "selected_remote_rank": source.get("selected_remote_rank"),
                "fallback_policy": source.get("fallback_policy"),
            }
            for source in sources
        ],
    }


def collect_manifesto_candidates(repo_path: Path) -> Dict[str, Any]:
    promotion = _load_json_file(
        repo_path / "data" / "inquiries" / "processed" / "latest_pattern_promotion.json"
    )
    pattern = _load_json_file(
        repo_path
        / "data"
        / "inquiries"
        / "processed"
        / "latest_pattern_distillation.json"
    )
    candidates: List[Dict[str, Any]] = []
    if promotion.get("generated"):
        for item in promotion.get("generated", [])[:5]:
            candidates.append(
                {
                    "candidate_type": "pattern_promotion",
                    "cluster_key": item.get("cluster_key"),
                    "report_ids": item.get("report_ids", []),
                    "wisdom_ids": item.get("wisdom_ids", []),
                    "suggested_shift": "提高对跨报告重复风险法则的优先级。",
                }
            )
    elif pattern.get("status") == "generated":
        candidates.append(
            {
                "candidate_type": "pattern_distillation",
                "report_id": pattern.get("report_id"),
                "wisdom_ids": pattern.get("distillation", {}).get("wisdom_ids", []),
                "suggested_shift": "将真实报告蒸馏结果纳入未来宣言偏置候选。",
            }
        )
    return {
        "status": "ready" if candidates else "idle",
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def collect_manifesto_draft_snapshot(repo_path: Path) -> Dict[str, Any]:
    return _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_draft_snapshot.json"
    )


def collect_manifesto_review(repo_path: Path) -> Dict[str, Any]:
    return _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_review.json"
    )


def collect_runtime_timeline(repo_path: Path) -> Dict[str, Any]:
    return _load_json_file(repo_path / "data" / "reports" / "runtime_timeline.json")


def collect_execution_trace(repo_path: Path) -> Dict[str, Any]:
    path = repo_path / "data" / "reports" / "execution_trace.jsonl"
    if not path.exists():
        return {"event_count": 0, "events": []}
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"event_count": len(events), "events": events[-50:]}


def collect_issue_ledger(repo_path: Path) -> Dict[str, Any]:
    return _load_json_file(
        repo_path / "data" / "reports" / "progress_issue_ledger.json"
    )


def collect_multi_site_capture_status(repo_path: Path) -> Dict[str, Any]:
    payload = _load_json_file(
        repo_path / "data" / "reports" / "multi_site_capture_report.json"
    )
    return {
        "status": payload.get("status", "missing"),
        "generated_at": payload.get("generated_at"),
        "site_summary": payload.get("site_summary", {}),
        "capture_summary": payload.get("capture_summary", {}),
        "quality_summary": {
            "average_site_quality_score": payload.get("capture_summary", {}).get(
                "average_site_quality_score"
            ),
            "rich_ready_sites": payload.get("capture_summary", {}).get(
                "rich_ready_sites"
            ),
            "quality_states": [
                {
                    "site_id": item.get("site_id"),
                    "quality_state": item.get("quality_state"),
                    "site_quality_score": item.get("site_quality_score"),
                }
                for item in payload.get("site_results", [])
            ],
        },
        "path_json": "data/reports/multi_site_capture_report.json",
    }


def collect_full_chain_acceptance_status(repo_path: Path) -> Dict[str, Any]:
    payload = _load_json_file(
        repo_path / "data" / "reports" / "full_chain_acceptance_report.json"
    )
    return {
        "status": payload.get("status", "missing"),
        "generated_at": payload.get("generated_at"),
        "acceptance_items": payload.get("acceptance_items", []),
        "recommendation": payload.get("recommendation"),
        "residual_risks": payload.get("residual_risks", []),
        "path_json": "data/reports/full_chain_acceptance_report.json",
        "path_markdown": "data/reports/full_chain_acceptance_report.md",
    }


def collect_reports_index_status(repo_path: Path) -> Dict[str, Any]:
    payload = _load_json_file(repo_path / "data" / "reports" / "reports_index.json")
    return {
        "status": payload.get("status", "missing"),
        "generated_at": payload.get("generated_at"),
        "moved_count": payload.get("moved_count"),
        "counts": payload.get("counts", {}),
        "path_json": "data/reports/reports_index.json",
        "path_markdown": "data/reports/reports_index.md",
    }


def _pick_latest_report(pattern: str, repo_path: Path) -> Dict[str, Any]:
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    for path in sorted((repo_path / "data" / "reports").glob(pattern)):
        payload = _load_json_file(path)
        if not payload:
            continue
        candidates.append((str(path), payload))
    if not candidates:
        return {}
    candidates.sort(
        key=lambda item: (
            str(item[1].get("generated_at") or ""),
            item[0],
        ),
        reverse=True,
    )
    payload = dict(candidates[0][1])
    payload["_resolved_path"] = candidates[0][0]
    return payload


def collect_report_registry(repo_path: Path) -> Dict[str, Any]:
    gate_report = _load_json_file(
        repo_path / "data" / "reports" / "foreign_trade_warning_gate_daily_report.json"
    )
    latest_trade_report = _pick_latest_report("real_data*_report.json", repo_path)
    pattern_report = _load_json_file(
        repo_path / "data" / "reports" / "pattern_distillation_daily_report.json"
    )
    pattern_batch_report = _load_json_file(
        repo_path / "data" / "reports" / "pattern_distillation_batch_report.json"
    )
    pattern_promotion = _load_json_file(
        repo_path / "data" / "inquiries" / "processed" / "latest_pattern_promotion.json"
    )
    manifesto_snapshot = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_draft_snapshot.json"
    )
    manifesto_compare = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_draft_compare.json"
    )
    manifesto_rewrite_candidate = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_rewrite_candidate.json"
    )
    manifesto_rewrite_simulation = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_rewrite_simulation.json"
    )
    manifesto_controlled_rewrite = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_controlled_rewrite.json"
    )
    manifesto_writeback_gate = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_formal_writeback_gate.json"
    )
    manifesto_writeback_authorization = _load_json_file(
        repo_path
        / "data"
        / "reports"
        / "decision_manifesto_formal_writeback_authorization.json"
    )
    manifesto_writeback_policy = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_writeback_policy.json"
    )
    manifesto_happy_path_drill = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_happy_path_drill.json"
    )
    manifesto_manual_review = _load_json_file(
        repo_path / "data" / "reports" / "decision_manifesto_manual_review.json"
    )
    multi_site_capture = _load_json_file(
        repo_path / "data" / "reports" / "multi_site_capture_report.json"
    )
    full_chain_acceptance = _load_json_file(
        repo_path / "data" / "reports" / "full_chain_acceptance_report.json"
    )
    reports_index = _load_json_file(
        repo_path / "data" / "reports" / "reports_index.json"
    )
    runtime_external_risk_audit = _load_json_file(
        repo_path / "data" / "reports" / "runtime_external_risk_audit.json"
    )
    history: List[Dict[str, Any]] = []
    if gate_report:
        history.append(
            {
                "report_id": gate_report.get("report_id"),
                "report_type": "gate_daily",
                "status": "generated",
                "generated_at": gate_report.get("generated_at"),
            }
        )
    if latest_trade_report:
        history.append(
            {
                "report_id": latest_trade_report.get("report_id"),
                "report_type": "real_trade_validation",
                "status": "generated",
                "generated_at": latest_trade_report.get("generated_at"),
                "notable_flags": [
                    "compliance-overflow",
                    "coordination_adjustments",
                    "2_medium_8_low",
                ],
            }
        )
    if pattern_report:
        latest = pattern_report.get("latest", {})
        history.append(
            {
                "report_id": latest.get("report_id"),
                "report_type": "pattern_distillation_daily",
                "status": latest.get("status", "generated"),
                "generated_at": pattern_report.get("generated_at"),
                "notable_flags": [
                    "trade_report_pattern",
                    "wisdom_distilled",
                ],
            }
        )
    if pattern_batch_report:
        history.append(
            {
                "report_id": f"batch:{pattern_batch_report.get('generated_at')}",
                "report_type": "pattern_distillation_batch",
                "status": "generated",
                "generated_at": pattern_batch_report.get("generated_at"),
                "notable_flags": [
                    f"generated:{pattern_batch_report.get('generated_count', 0)}",
                    f"skipped:{pattern_batch_report.get('skipped_count', 0)}",
                ],
            }
        )
    if pattern_promotion:
        history.append(
            {
                "report_id": f"promotion:{pattern_promotion.get('generated_at')}",
                "report_type": "pattern_promotion",
                "status": pattern_promotion.get("status", "generated"),
                "generated_at": pattern_promotion.get("generated_at"),
                "notable_flags": [
                    f"clusters:{len(pattern_promotion.get('generated', []))}",
                ],
            }
        )
    if manifesto_snapshot:
        history.append(
            {
                "report_id": manifesto_snapshot.get("version_id"),
                "report_type": "manifesto_draft_snapshot",
                "status": manifesto_snapshot.get("status", "generated"),
                "generated_at": manifesto_snapshot.get("generated_at"),
                "notable_flags": [
                    f"candidates:{len(manifesto_snapshot.get('candidates', []))}",
                ],
            }
        )
    if manifesto_rewrite_candidate:
        history.append(
            {
                "report_id": manifesto_rewrite_candidate.get("version_id"),
                "report_type": "manifesto_rewrite_candidate",
                "status": manifesto_rewrite_candidate.get("status"),
                "generated_at": manifesto_rewrite_candidate.get("generated_at"),
                "notable_flags": [
                    f"approval:{manifesto_rewrite_candidate.get('approval_status')}",
                ],
            }
        )
    if manifesto_rewrite_simulation:
        history.append(
            {
                "report_id": manifesto_rewrite_simulation.get("version_id"),
                "report_type": "manifesto_rewrite_simulation",
                "status": manifesto_rewrite_simulation.get("status"),
                "generated_at": manifesto_rewrite_simulation.get("generated_at"),
                "notable_flags": [
                    f"approval:{manifesto_rewrite_simulation.get('approval_status')}",
                ],
            }
        )
    if manifesto_manual_review:
        history.append(
            {
                "report_id": f"manual_review:{manifesto_rewrite_candidate.get('version_id') or 'current'}",
                "report_type": "manifesto_manual_review",
                "status": manifesto_manual_review.get("status"),
                "generated_at": manifesto_manual_review.get("updated_at"),
                "notable_flags": [
                    f"approved:{manifesto_manual_review.get('approved')}",
                ],
            }
        )
    if manifesto_writeback_gate:
        history.append(
            {
                "report_id": f"writeback_gate:{manifesto_writeback_gate.get('version_id') or 'current'}",
                "report_type": "manifesto_formal_writeback_gate",
                "status": manifesto_writeback_gate.get("status"),
                "generated_at": manifesto_writeback_gate.get("generated_at"),
                "notable_flags": [
                    f"authorization:{manifesto_writeback_gate.get('formal_authorization', {}).get('approved')}",
                ],
            }
        )
    if manifesto_writeback_authorization:
        history.append(
            {
                "report_id": f"writeback_authorization:{manifesto_writeback_authorization.get('version_id') or 'current'}",
                "report_type": "manifesto_formal_writeback_authorization",
                "status": manifesto_writeback_authorization.get("status"),
                "generated_at": manifesto_writeback_authorization.get("updated_at"),
                "notable_flags": [
                    f"approved:{manifesto_writeback_authorization.get('approved')}",
                ],
            }
        )
    if manifesto_writeback_policy:
        history.append(
            {
                "report_id": f"writeback_policy:{manifesto_writeback_policy.get('decision') or 'current'}",
                "report_type": "manifesto_writeback_policy",
                "status": manifesto_writeback_policy.get("status"),
                "generated_at": manifesto_writeback_policy.get("updated_at"),
                "notable_flags": [
                    f"decision:{manifesto_writeback_policy.get('decision')}",
                    f"formal_allowed:{manifesto_writeback_policy.get('formal_writeback_allowed')}",
                ],
            }
        )
    if manifesto_controlled_rewrite:
        history.append(
            {
                "report_id": manifesto_controlled_rewrite.get("version_id"),
                "report_type": "manifesto_controlled_rewrite",
                "status": manifesto_controlled_rewrite.get("status"),
                "generated_at": manifesto_controlled_rewrite.get("generated_at"),
                "notable_flags": [
                    f"approval:{manifesto_controlled_rewrite.get('approval_status')}",
                ],
            }
        )
    if manifesto_happy_path_drill:
        history.append(
            {
                "report_id": f"manifesto_happy_path:{manifesto_happy_path_drill.get('version_id') or 'current'}",
                "report_type": "manifesto_happy_path_drill",
                "status": manifesto_happy_path_drill.get("status"),
                "generated_at": manifesto_happy_path_drill.get("generated_at"),
                "notable_flags": [
                    f"formal_writeback:{manifesto_happy_path_drill.get('formal_writeback_applied')}",
                ],
            }
        )
    if multi_site_capture:
        history.append(
            {
                "report_id": f"multi_site_capture:{multi_site_capture.get('generated_at')}",
                "report_type": "multi_site_capture",
                "status": multi_site_capture.get("status"),
                "generated_at": multi_site_capture.get("generated_at"),
                "notable_flags": [
                    f"sites:{multi_site_capture.get('capture_summary', {}).get('site_count', 0)}",
                    f"success:{multi_site_capture.get('capture_summary', {}).get('successful_sites', 0)}",
                ],
            }
        )
    if full_chain_acceptance:
        history.append(
            {
                "report_id": f"full_chain_acceptance:{full_chain_acceptance.get('generated_at')}",
                "report_type": "full_chain_acceptance",
                "status": full_chain_acceptance.get("status"),
                "generated_at": full_chain_acceptance.get("generated_at"),
                "notable_flags": [
                    f"residual_risks:{len(full_chain_acceptance.get('residual_risks', []))}",
                ],
            }
        )
    if reports_index:
        history.append(
            {
                "report_id": f"reports_index:{reports_index.get('generated_at')}",
                "report_type": "reports_index",
                "status": reports_index.get("status"),
                "generated_at": reports_index.get("generated_at"),
                "notable_flags": [
                    f"moved:{reports_index.get('moved_count', 0)}",
                    f"root_json:{reports_index.get('counts', {}).get('reports_root', 0)}",
                ],
            }
        )
    if runtime_external_risk_audit:
        history.append(
            {
                "report_id": f"external_risk_audit:{runtime_external_risk_audit.get('generated_at')}",
                "report_type": "runtime_external_risk_audit",
                "status": runtime_external_risk_audit.get("status"),
                "generated_at": runtime_external_risk_audit.get("generated_at"),
                "notable_flags": [
                    f"fallback_sources:{len(runtime_external_risk_audit.get('fallback_sources', []))}",
                    f"conflict_sources:{len(runtime_external_risk_audit.get('conflict_summary', {}).get('ordered_sources', []))}",
                ],
            }
        )
    compact_history = _compact_history_entries(history, limit=12)
    return {
        "latest_gate_report": {
            "report_id": gate_report.get("report_id"),
            "generated_at": gate_report.get("generated_at"),
            "sample_count": gate_report.get("sample_count"),
            "blocked_count": gate_report.get("summary", {}).get("blocked_count"),
            "path_json": "data/reports/foreign_trade_warning_gate_daily_report.json",
            "path_markdown": "data/reports/foreign_trade_warning_gate_daily_report.md",
        },
        "latest_real_trade_report": {
            "report_id": latest_trade_report.get("report_id"),
            "generated_at": latest_trade_report.get("generated_at"),
            "sample_count": latest_trade_report.get("sample_count"),
            "medium_count": latest_trade_report.get("summary", {}).get("medium_count"),
            "low_count": latest_trade_report.get("summary", {}).get("low_count"),
            "dominant_risk_vectors": latest_trade_report.get("summary", {}).get(
                "dominant_risk_vectors"
            ),
            "path_json": str(latest_trade_report.get("_resolved_path") or ""),
            "path_markdown": str(
                Path(str(latest_trade_report.get("_resolved_path") or "")).with_suffix(
                    ".md"
                )
            )
            if latest_trade_report.get("_resolved_path")
            else "",
        },
        "latest_pattern_distillation_report": {
            "report_id": pattern_report.get("latest", {}).get("report_id"),
            "generated_at": pattern_report.get("generated_at"),
            "candidate_count": pattern_report.get("latest", {}).get("candidate_count"),
            "wisdom_ids": pattern_report.get("latest", {})
            .get("distillation", {})
            .get("wisdom_ids", []),
            "path_json": "data/reports/pattern_distillation_daily_report.json",
            "path_markdown": "data/reports/pattern_distillation_daily_report.md",
        },
        "latest_pattern_batch_report": {
            "generated_at": pattern_batch_report.get("generated_at"),
            "batch_count": pattern_batch_report.get("batch_count"),
            "generated_count": pattern_batch_report.get("generated_count"),
            "skipped_count": pattern_batch_report.get("skipped_count"),
            "path_json": "data/reports/pattern_distillation_batch_report.json",
            "path_markdown": "data/reports/pattern_distillation_batch_report.md",
        },
        "latest_pattern_promotion": {
            "generated_at": pattern_promotion.get("generated_at"),
            "status": pattern_promotion.get("status"),
            "generated": pattern_promotion.get("generated", []),
            "path_json": "data/inquiries/processed/latest_pattern_promotion.json",
        },
        "latest_manifesto_draft_snapshot": {
            "version_id": manifesto_snapshot.get("version_id"),
            "generated_at": manifesto_snapshot.get("generated_at"),
            "status": manifesto_snapshot.get("status"),
            "candidate_count": len(manifesto_snapshot.get("candidates", [])),
            "path_json": "data/reports/decision_manifesto_draft_snapshot.json",
            "path_markdown": "data/reports/decision_manifesto_draft_snapshot.md",
        },
        "latest_manifesto_draft_compare": {
            "generated_at": manifesto_compare.get("generated_at"),
            "current_version": manifesto_compare.get("current_version"),
            "previous_version": manifesto_compare.get("previous_version"),
            "draft_changed": manifesto_compare.get("draft_changed"),
            "path_json": "data/reports/decision_manifesto_draft_compare.json",
            "path_markdown": "data/reports/decision_manifesto_draft_compare.md",
        },
        "latest_manifesto_rewrite_candidate": {
            "version_id": manifesto_rewrite_candidate.get("version_id"),
            "generated_at": manifesto_rewrite_candidate.get("generated_at"),
            "status": manifesto_rewrite_candidate.get("status"),
            "approval_status": manifesto_rewrite_candidate.get("approval_status"),
            "path_json": "data/reports/decision_manifesto_rewrite_candidate.json",
            "path_markdown": "data/reports/decision_manifesto_rewrite_candidate.md",
        },
        "latest_manifesto_rewrite_simulation": {
            "version_id": manifesto_rewrite_simulation.get("version_id"),
            "generated_at": manifesto_rewrite_simulation.get("generated_at"),
            "status": manifesto_rewrite_simulation.get("status"),
            "approval_status": manifesto_rewrite_simulation.get("approval_status"),
            "candidate_count": manifesto_rewrite_simulation.get("candidate_count"),
            "path_json": "data/reports/decision_manifesto_rewrite_simulation.json",
            "path_markdown": "data/reports/decision_manifesto_rewrite_simulation.md",
        },
        "latest_manifesto_controlled_rewrite": {
            "version_id": manifesto_controlled_rewrite.get("version_id"),
            "generated_at": manifesto_controlled_rewrite.get("generated_at"),
            "status": manifesto_controlled_rewrite.get("status"),
            "approval_status": manifesto_controlled_rewrite.get("approval_status"),
            "candidate_count": manifesto_controlled_rewrite.get("candidate_count"),
            "path_json": "data/reports/decision_manifesto_controlled_rewrite.json",
            "path_markdown": "data/reports/decision_manifesto_controlled_rewrite.md",
        },
        "latest_manifesto_manual_review": {
            "status": manifesto_manual_review.get("status"),
            "approved": manifesto_manual_review.get("approved"),
            "reviewer": manifesto_manual_review.get("reviewer"),
            "updated_at": manifesto_manual_review.get("updated_at"),
            "path_json": "data/reports/decision_manifesto_manual_review.json",
            "path_markdown": "data/reports/decision_manifesto_manual_review.md",
        },
        "latest_manifesto_formal_writeback_gate": {
            "generated_at": manifesto_writeback_gate.get("generated_at"),
            "status": manifesto_writeback_gate.get("status"),
            "version_id": manifesto_writeback_gate.get("version_id"),
            "approval_status": manifesto_writeback_gate.get("approval_status"),
            "policy_decision": manifesto_writeback_gate.get("policy", {}).get(
                "decision"
            ),
            "policy_allows_formal_writeback": manifesto_writeback_gate.get(
                "gate_breakdown", {}
            ).get("policy_allows_formal_writeback"),
            "human_status": _build_manifesto_policy_human_status(
                manifesto_writeback_policy,
                manifesto_writeback_gate,
            ),
            "path_json": "data/reports/decision_manifesto_formal_writeback_gate.json",
            "path_markdown": "data/reports/decision_manifesto_formal_writeback_gate.md",
        },
        "latest_manifesto_formal_writeback_authorization": {
            "status": manifesto_writeback_authorization.get("status"),
            "approved": manifesto_writeback_authorization.get("approved"),
            "version_id": manifesto_writeback_authorization.get("version_id"),
            "reviewer": manifesto_writeback_authorization.get("reviewer"),
            "updated_at": manifesto_writeback_authorization.get("updated_at"),
            "path_json": "data/reports/decision_manifesto_formal_writeback_authorization.json",
            "path_markdown": "data/reports/decision_manifesto_formal_writeback_authorization.md",
        },
        "latest_manifesto_writeback_policy": {
            "status": manifesto_writeback_policy.get("status"),
            "decision": manifesto_writeback_policy.get("decision"),
            "formal_writeback_allowed": manifesto_writeback_policy.get(
                "formal_writeback_allowed"
            ),
            "comment": manifesto_writeback_policy.get("comment"),
            "allow_when_count": len(manifesto_writeback_policy.get("allow_when", [])),
            "deny_when_count": len(manifesto_writeback_policy.get("deny_when", [])),
            "human_status": _build_manifesto_policy_human_status(
                manifesto_writeback_policy,
                manifesto_writeback_gate,
            ),
            "reviewer": manifesto_writeback_policy.get("reviewer"),
            "updated_at": manifesto_writeback_policy.get("updated_at"),
            "path_json": "data/reports/decision_manifesto_writeback_policy.json",
            "path_markdown": "data/reports/decision_manifesto_writeback_policy.md",
        },
        "latest_manifesto_happy_path_drill": {
            "generated_at": manifesto_happy_path_drill.get("generated_at"),
            "status": manifesto_happy_path_drill.get("status"),
            "version_id": manifesto_happy_path_drill.get("version_id"),
            "formal_writeback_applied": manifesto_happy_path_drill.get(
                "formal_writeback_applied"
            ),
            "path_json": "data/reports/decision_manifesto_happy_path_drill.json",
            "path_markdown": "data/reports/decision_manifesto_happy_path_drill.md",
        },
        "latest_multi_site_capture": {
            "generated_at": multi_site_capture.get("generated_at"),
            "status": multi_site_capture.get("status"),
            "site_count": multi_site_capture.get("capture_summary", {}).get(
                "site_count"
            ),
            "successful_sites": multi_site_capture.get("capture_summary", {}).get(
                "successful_sites"
            ),
            "average_quality_score": multi_site_capture.get("site_summary", {}).get(
                "average_quality_score"
            ),
            "average_site_quality_score": multi_site_capture.get(
                "capture_summary", {}
            ).get("average_site_quality_score"),
            "rich_ready_sites": multi_site_capture.get("capture_summary", {}).get(
                "rich_ready_sites"
            ),
            "path_json": "data/reports/multi_site_capture_report.json",
        },
        "latest_full_chain_acceptance": {
            "generated_at": full_chain_acceptance.get("generated_at"),
            "status": full_chain_acceptance.get("status"),
            "acceptance_item_count": len(
                full_chain_acceptance.get("acceptance_items", [])
            ),
            "residual_risk_count": len(full_chain_acceptance.get("residual_risks", [])),
            "path_json": "data/reports/full_chain_acceptance_report.json",
            "path_markdown": "data/reports/full_chain_acceptance_report.md",
        },
        "latest_reports_index": {
            "generated_at": reports_index.get("generated_at"),
            "status": reports_index.get("status"),
            "moved_count": reports_index.get("moved_count"),
            "reports_root_count": reports_index.get("counts", {}).get("reports_root"),
            "path_json": "data/reports/reports_index.json",
            "path_markdown": "data/reports/reports_index.md",
        },
        "latest_runtime_external_risk_audit": {
            "generated_at": runtime_external_risk_audit.get("generated_at"),
            "status": runtime_external_risk_audit.get("status"),
            "fallback_source_count": len(
                runtime_external_risk_audit.get("fallback_sources", [])
            ),
            "conflict_source_count": len(
                runtime_external_risk_audit.get("conflict_summary", {}).get(
                    "ordered_sources", []
                )
            ),
            "selected_source": runtime_external_risk_audit.get(
                "executive_summary", {}
            ).get("selected_source"),
            "suppressed_sources": runtime_external_risk_audit.get(
                "executive_summary", {}
            ).get("suppressed_sources", []),
            "resolution_basis": runtime_external_risk_audit.get(
                "executive_summary", {}
            ).get("resolution_basis"),
            "recommended_action": runtime_external_risk_audit.get(
                "executive_summary", {}
            ).get("recommended_action"),
            "human_status": runtime_external_risk_audit.get(
                "executive_summary", {}
            ).get("human_status"),
            "path_json": "data/reports/runtime_external_risk_audit.json",
        },
        "history_summary": {
            "raw_count": len(history),
            "retained_count": len(compact_history),
            "deduped": len(history) - len(compact_history),
        },
        "history": compact_history,
    }


def collect_module_inventory(module_edges: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    src_modules = [
        item
        for item in module_edges
        if str(item.get("module", "")).startswith("src.") or item.get("module") == "src"
    ]
    script_modules = [
        item
        for item in module_edges
        if str(item.get("module", "")).startswith("scripts.")
        or item.get("module") == "scripts"
    ]
    return {
        "src_python_modules": len(src_modules),
        "script_modules": len(script_modules),
        "total_runtime_modules": len(module_edges),
        "counting_rule": "统计 src 与 scripts 目录下的 Python 模块文件，供 Web 前端展示系统版图规模。",
    }


def collect_update_history(
    repo_path: Path, heartbeat: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    heartbeat_payload = heartbeat or _load_json_file(repo_path / ".abu.heartbeat")
    history = [
        {
            "id": "update:chronos-always-on-20260315-1242",
            "timestamp": heartbeat_payload.get("timestamp")
            or datetime.now().isoformat(),
            "type": "runtime_restart",
            "title": "巡航恢复为无限期运行",
            "summary": "移除过期固定截止时间，重新启动主巡航，并恢复 heartbeat 持续刷新。",
            "status": heartbeat_payload.get("phase") or "unknown",
            "evidence": [
                "src/main_production.py",
                ".abu.heartbeat",
            ],
        },
        {
            "id": "update:m8-hardening-20260315-1216",
            "timestamp": "2026-03-15T12:16:00",
            "type": "runtime_hardening",
            "title": "M8/M14 第一轮物理治理完成",
            "summary": "加入 sandbox TTL、recovering 状态机与 action_journal 自动轮转。",
            "status": "completed",
            "evidence": [
                "src/execution/sandbox.py",
                "src/main_production.py",
                "src/observability/action_journal.py",
            ],
        },
        {
            "id": "update:trade-warning-overflow-20260315-1121",
            "timestamp": "2026-03-15T11:21:54",
            "type": "risk_engine_upgrade",
            "title": "合规单项溢出补偿生效",
            "summary": "协调层已支持 compliance-overflow，真实外贸样本出现 2 medium + 8 low 分化。",
            "status": "completed",
            "evidence": [
                "src/social/trade_warning.py",
                "data/reports/real_data_trade_leads_enriched_20260315_0230_report.json",
            ],
        },
    ]
    recovery = heartbeat_payload.get("last_recovery") or {}
    recovery_actions = heartbeat_payload.get("recovery_actions") or []
    if recovery:
        history.insert(
            0,
            {
                "id": f"update:recovery:{recovery.get('at', 'unknown')}",
                "timestamp": recovery.get("finished_at")
                or recovery.get("at")
                or datetime.now().isoformat(),
                "type": "runtime_recovery",
                "title": "最近一次恢复动作执行结果",
                "summary": recovery.get("summary")
                or recovery.get("error")
                or "恢复事件已记录。",
                "status": recovery.get("status") or "unknown",
                "evidence": [
                    item.get("name") for item in recovery_actions if item.get("name")
                ],
                "actions": recovery_actions,
            },
        )
    pattern_distillation = heartbeat_payload.get("pattern_distillation") or {}
    if not pattern_distillation:
        pattern_distillation = _load_json_file(
            repo_path
            / "data"
            / "inquiries"
            / "processed"
            / "latest_pattern_distillation.json"
        )
    if pattern_distillation.get("status") == "generated":
        history.insert(
            0,
            {
                "id": f"update:pattern-distillation:{pattern_distillation.get('report_id', 'unknown')}",
                "timestamp": heartbeat_payload.get("timestamp")
                or datetime.now().isoformat(),
                "type": "pattern_distillation",
                "title": "真实报告风险模式已蒸馏为知识节点",
                "summary": (
                    f"报告 {pattern_distillation.get('report_id')} 提炼出 "
                    f"{pattern_distillation.get('candidate_count', 0)} 条模式候选，"
                    f"生成 wisdom {pattern_distillation.get('distillation', {}).get('wisdom_ids', [])}。"
                ),
                "status": "completed",
                "evidence": [
                    pattern_distillation.get("report_id"),
                    *[
                        f"memory:{memory_id}"
                        for memory_id in pattern_distillation.get("memory_ids", [])
                    ],
                ],
            },
        )
    pattern_promotion = heartbeat_payload.get("pattern_promotion") or _load_json_file(
        repo_path / "data" / "inquiries" / "processed" / "latest_pattern_promotion.json"
    )
    if pattern_promotion.get("status") == "generated":
        history.insert(
            0,
            {
                "id": f"update:pattern-promotion:{pattern_promotion.get('generated_at', 'unknown')}",
                "timestamp": pattern_promotion.get("generated_at")
                or datetime.now().isoformat(),
                "type": "pattern_promotion",
                "title": "模式簇已升格为高层风险法则",
                "summary": (
                    f"本轮升格 {len(pattern_promotion.get('generated', []))} 个模式簇，"
                    f"生成高层法则节点。"
                ),
                "status": "completed",
                "evidence": [
                    item.get("cluster_key")
                    for item in pattern_promotion.get("generated", [])
                    if item.get("cluster_key")
                ],
            },
        )
    return _compact_history_entries(history, limit=8)


def _compact_history_entries(
    history: Sequence[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    ranked = sorted(
        history,
        key=lambda item: str(item.get("generated_at") or item.get("timestamp") or ""),
        reverse=True,
    )
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in ranked:
        key = (
            str(item.get("report_type") or item.get("type") or "unknown"),
            str(item.get("report_id") or item.get("id") or "unknown"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def collect_update_protocol() -> Dict[str, Any]:
    return {
        "owner_rule": "以后每次升级、更新或新增时，必须沿用本文件的顶层结构追加，不得删除前端依赖层。",
        "append_rules": [
            "新增知识节点追加到对应层数组，不改旧节点 id",
            "新增运行态信息优先写入 runtime_status 与 report_registry",
            "新增模块进度只更新 module_progress 中对应 module_id，保持 module_id 稳定",
        ],
        "front_end_safe_fields": [
            "schema_version",
            "generated_at",
            "module_inventory",
            "wisdom_nodes",
            "vector_groups",
            "activity_nodes",
            "source_materials",
            "view_config",
            "module_progress",
            "external_risk_sources",
            "manifesto_candidates",
            "manifesto_draft_snapshot",
            "manifesto_review",
            "runtime_timeline",
            "issue_ledger",
            "runtime_status",
            "report_registry",
        ],
        "breaking_change_policy": "若必须调整字段名，必须保留旧字段至少一个兼容版本，并同步提升 schema_version。",
    }


def _load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_source_materials_dedup_report(
    repo_path: Path,
    dedup_payload: Dict[str, Any],
) -> None:
    reports_dir = repo_path / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "source_materials_dedup_report.json"
    report = {
        "generated_at": datetime.now().isoformat(),
        "status": dedup_payload.get("status", "clean"),
        "dedup": dedup_payload,
    }
    _write_json_atomic(report_path, report)


def _build_manifesto_policy_human_status(
    policy: Dict[str, Any],
    gate: Dict[str, Any],
) -> str:
    decision = str(policy.get("decision") or "").strip() or "unknown"
    gate_status = str(gate.get("status") or "").strip() or "unknown"
    if decision == "drill_only":
        return "当前仅允许 happy-path 演练，正式写回默认受策略阻断。"
    if decision == "formal_allowed" and gate_status == "approved_for_formal_writeback":
        return "当前制度已允许正式写回，且本轮候选满足正式回写条件。"
    if decision == "formal_allowed":
        return "当前制度允许正式写回，但本轮候选尚未满足全部放行条件。"
    return "当前宣言写回策略未形成清晰结论，需人工复核。"


def export_evolution_map(
    memory_manager: MemoryManager,
    output_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    src_root: Optional[str] = None,
    integrity_manager: Optional[IntegrityManager] = None,
    brain: Any = None,
) -> Dict[str, Any]:
    repo_path = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    source_path = Path(src_root) if src_root else repo_path / "src"
    scripts_path = repo_path / "scripts"
    destination = (
        Path(output_path) if output_path else repo_path / DEFAULT_OUTPUT_FILENAME
    )
    code_roots = [source_path]
    if scripts_path.exists():
        code_roots.append(scripts_path)

    source_materials, source_materials_dedup = _build_source_material_payload(
        memory_manager
    )
    _write_source_materials_dedup_report(repo_path, source_materials_dedup)
    payload = {
        "schema_version": MAP_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "map_contract": collect_map_contract(),
        "timeline": collect_git_timeline(
            str(repo_path), integrity_manager=integrity_manager
        ),
        "module_edges": scan_code_roots(code_roots),
        "wisdom_nodes": collect_wisdom_nodes(memory_manager),
        "vector_groups": collect_vector_groups(memory_manager)
        + collect_pattern_cluster_nodes(repo_path),
        "activity_nodes": collect_activity_nodes(memory_manager),
        "source_materials": source_materials,
        "source_materials_dedup": source_materials_dedup,
        "view_config": {
            "default_visible_layers": ["wisdom_nodes"],
            "default_hidden_layers": [
                "vector_groups",
                "activity_nodes",
                "source_materials",
            ],
            "details_expand_layers": [
                "vector_groups",
                "activity_nodes",
                "source_materials",
            ],
            "stable_read_layers": [
                "wisdom_nodes",
                "vector_groups",
                "activity_nodes",
                "source_materials",
            ],
            "front_end_mode": "layered_map",
        },
        "module_inventory": {},
        "module_progress": collect_module_progress(),
        "external_risk_sources": collect_external_risk_sources(repo_path),
        "manifesto_candidates": collect_manifesto_candidates(repo_path),
        "manifesto_draft_snapshot": collect_manifesto_draft_snapshot(repo_path),
        "manifesto_review": collect_manifesto_review(repo_path),
        "runtime_timeline": collect_runtime_timeline(repo_path),
        "execution_trace": collect_execution_trace(repo_path),
        "issue_ledger": collect_issue_ledger(repo_path),
        "runtime_status": collect_runtime_status(repo_path),
        "report_registry": collect_report_registry(repo_path),
        "site_capture_status": collect_multi_site_capture_status(repo_path),
        "full_chain_acceptance_status": collect_full_chain_acceptance_status(repo_path),
        "reports_index_status": collect_reports_index_status(repo_path),
        "knowledge_classification_status": collect_knowledge_classification_status(
            repo_path
        ),
        "brain_state": collect_brain_state(brain),
        "update_protocol": collect_update_protocol(),
    }
    payload["module_inventory"] = collect_module_inventory(payload["module_edges"])
    payload = _normalize_export_payload(payload)
    _write_json_atomic(destination, payload)
    return payload


def main() -> int:
    repo_path = Path(__file__).resolve().parents[2]
    memory_manager = MemoryManager()
    payload = export_evolution_map(memory_manager, repo_root=str(repo_path))
    output_path = repo_path / DEFAULT_OUTPUT_FILENAME
    print(
        json.dumps(
            {
                "output_path": str(output_path),
                "timeline": len(payload["timeline"]),
                "module_edges": len(payload["module_edges"]),
                "wisdom_nodes": len(payload["wisdom_nodes"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _normalize_export_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = _sanitize_payload_text(payload)
    _normalize_timeline_timestamps(sanitized.get("timeline", []))
    _normalize_module_edge_timestamps(sanitized.get("module_edges", []))
    _normalize_wisdom_node_timestamps(sanitized.get("wisdom_nodes", []))
    _normalize_simple_node_timestamps(
        sanitized.get("activity_nodes", []), field="created_at"
    )
    _normalize_simple_node_timestamps(
        sanitized.get("source_materials", []), field="created_at"
    )
    _normalize_simple_node_timestamps(
        sanitized.get("vector_groups", []), field="created_at"
    )
    return sanitized


def _sanitize_payload_text(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_payload_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload_text(item) for item in value]
    if isinstance(value, str):
        sanitized = value
        for old, new in _DISPLAY_TEXT_REPLACEMENTS:
            sanitized = sanitized.replace(old, new)
        return sanitized
    return value


def _normalize_timeline_timestamps(entries: List[Dict[str, Any]]) -> None:
    ordered = sorted(
        range(len(entries)),
        key=lambda index: (
            _parse_iso_datetime(entries[index].get("committed_at")),
            index,
        ),
    )
    adjusted = _stagger_datetimes(
        [_parse_iso_datetime(entries[index].get("committed_at")) for index in ordered]
    )
    for index, dt in zip(ordered, adjusted):
        if dt is not None:
            entries[index]["committed_at"] = dt.isoformat()


def _normalize_module_edge_timestamps(entries: List[Dict[str, Any]]) -> None:
    ordered = sorted(
        range(len(entries)),
        key=lambda index: (
            _parse_iso_datetime(entries[index].get("updated_at")),
            str(entries[index].get("module", "")),
        ),
    )
    adjusted = _stagger_datetimes(
        [_parse_iso_datetime(entries[index].get("updated_at")) for index in ordered]
    )
    for index, dt in zip(ordered, adjusted):
        if dt is not None:
            entries[index]["updated_at"] = dt.isoformat()


def _normalize_wisdom_node_timestamps(nodes: List[Dict[str, Any]]) -> None:
    global_contains: List[Tuple[datetime, int, Dict[str, Any]]] = []
    for node in nodes:
        contains = node.get("contains", []) or []
        contains_ordered = sorted(
            contains,
            key=lambda item: (
                _parse_iso_datetime(item.get("created_at")),
                int(item.get("id", 0)),
            ),
        )
        node["contains"] = contains_ordered
        for item in contains_ordered:
            parsed = _parse_iso_datetime(item.get("created_at"))
            if parsed is not None:
                global_contains.append((parsed, int(item.get("id", 0)), item))

    global_contains.sort(key=lambda entry: (entry[0], entry[1]))
    adjusted_contains = _stagger_datetimes([entry[0] for entry in global_contains])
    for (_, _, item), dt in zip(global_contains, adjusted_contains):
        if dt is not None:
            item["created_at"] = dt.isoformat()

    node_seed_times: List[Optional[datetime]] = []
    for node in nodes:
        contains = node.get("contains", []) or []
        if contains:
            node_seed_times.append(_parse_iso_datetime(contains[0].get("created_at")))
        else:
            node_seed_times.append(None)

    ordered = sorted(
        range(len(nodes)),
        key=lambda index: (
            node_seed_times[index] or datetime(1970, 1, 1),
            str(nodes[index].get("id", "")),
        ),
    )
    adjusted_nodes = _stagger_datetimes([node_seed_times[index] for index in ordered])
    for index, dt in zip(ordered, adjusted_nodes):
        if dt is not None:
            nodes[index]["created_at"] = dt.isoformat()


def _normalize_simple_node_timestamps(
    nodes: List[Dict[str, Any]], field: str = "created_at"
) -> None:
    ordered = sorted(
        range(len(nodes)),
        key=lambda index: (
            _parse_iso_datetime(nodes[index].get(field)) or datetime(1970, 1, 1),
            str(nodes[index].get("id", "")),
        ),
    )
    adjusted = _stagger_datetimes(
        [_parse_iso_datetime(nodes[index].get(field)) for index in ordered]
    )
    for index, dt in zip(ordered, adjusted):
        if dt is not None:
            nodes[index][field] = dt.isoformat()


def _stagger_datetimes(
    values: Sequence[Optional[datetime]],
) -> List[Optional[datetime]]:
    adjusted: List[Optional[datetime]] = []
    previous: Optional[datetime] = None
    for value in values:
        if value is None:
            adjusted.append(None)
            continue
        candidate = value.replace(microsecond=0)
        if previous is not None and candidate <= previous:
            candidate = previous + timedelta(seconds=1)
        elif previous is not None and (candidate - previous).total_seconds() < 1.0:
            candidate = previous + timedelta(seconds=1)
        adjusted.append(candidate)
        previous = candidate
    return adjusted


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _extract_topic_tokens(text: str) -> List[str]:
    cleaned = re.sub(
        r"[，。！？；：、,.!?;:()（）\[\]{}\-_/\\]+", " ", (text or "").strip().lower()
    )
    chunks = [
        chunk
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", cleaned)
        if chunk not in _STOPWORDS
    ]
    tokens: List[str] = []
    for chunk in chunks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            tokens.append(chunk)
            if len(chunk) > 4:
                tokens.extend(
                    chunk[index : index + 2] for index in range(len(chunk) - 1)
                )
        else:
            tokens.append(chunk)
    return sorted(token for token in tokens if token and token not in _STOPWORDS)


def _wisdom_similarity(left: SemanticWisdom, right: SemanticWisdom) -> float:
    text_similarity = calculate_similarity(
        left.wisdom_text or "", right.wisdom_text or ""
    )
    left_tokens = set(_extract_topic_tokens(left.wisdom_text or ""))
    right_tokens = set(_extract_topic_tokens(right.wisdom_text or ""))
    token_overlap = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if (left_tokens or right_tokens)
        else 0.0
    )
    category_match = (
        0.1 if (left.category or "learning") == (right.category or "learning") else 0.0
    )
    return round(0.55 * text_similarity + 0.35 * token_overlap + category_match, 6)


def _cluster_wisdom_records(
    records: Sequence[SemanticWisdom],
) -> List[List[SemanticWisdom]]:
    buckets: Dict[str, List[SemanticWisdom]] = defaultdict(list)
    for record in records:
        buckets[record.category or "learning"].append(record)

    clustered: List[List[SemanticWisdom]] = []
    for category in sorted(buckets):
        category_records = sorted(
            buckets[category],
            key=lambda record: (
                _stable_created_at(record),
                record.id or 0,
            ),
        )
        adjacency: Dict[int, set[int]] = {
            index: set() for index in range(len(category_records))
        }
        for left_index, left_record in enumerate(category_records):
            for right_index in range(left_index + 1, len(category_records)):
                right_record = category_records[right_index]
                if (
                    _wisdom_similarity(left_record, right_record)
                    >= _WISDOM_CLUSTER_SIMILARITY_THRESHOLD
                ):
                    adjacency[left_index].add(right_index)
                    adjacency[right_index].add(left_index)

        visited = set()
        for index in range(len(category_records)):
            if index in visited:
                continue
            stack = [index]
            component: List[SemanticWisdom] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(category_records[current])
                stack.extend(sorted(adjacency[current] - visited, reverse=True))
            clustered.append(_sort_cluster_records(component))

    return sorted(
        clustered,
        key=lambda cluster: _build_cluster_id(
            cluster[0].category or "learning",
            sorted(record.id for record in cluster if record.id is not None),
        ),
    )


def _generate_anchor(cluster_records: Sequence[SemanticWisdom]) -> str:
    ordered_records = _sort_cluster_records(cluster_records)
    token_counter = Counter(_cluster_tokens(ordered_records))
    token_set = set(token_counter)
    category = ordered_records[0].category or "learning"

    if category == "culture":
        culture_anchor = _match_culture_anchor(token_set)
        if culture_anchor is not None:
            return culture_anchor

    for keywords, anchor in _ANCHOR_RULES:
        if all(keyword in token_set for keyword in keywords):
            return anchor

    lexicon_anchor = _match_anchor_lexicon(category, token_set)
    if lexicon_anchor is not None:
        return lexicon_anchor

    lead_anchor = _extract_lead_anchor_keyword(ordered_records[0].wisdom_text or "")
    if lead_anchor is not None:
        return lead_anchor

    long_tokens = [
        token
        for token, _ in token_counter.most_common()
        if _is_semantic_anchor_token(token)
    ]
    if long_tokens:
        candidate = _normalize_anchor_token(long_tokens[0])
        if _contains_connector(candidate):
            fallback = _extract_lead_anchor_keyword(
                ordered_records[0].wisdom_text or ""
            )
            if fallback is not None:
                return fallback
        return candidate

    prefix = _CATEGORY_FALLBACK_PREFIX.get(category, "学域")
    seed = str(ordered_records[0].id or 0).zfill(2)
    return f"{prefix}{seed[-2:]}"


def _generate_topic_summary(
    cluster_records: Sequence[SemanticWisdom],
    *,
    anchor: str = "",
) -> str:
    ordered_records = _sort_cluster_records(cluster_records)
    lead = max(
        ordered_records,
        key=lambda record: (
            record.importance or 0.0,
            -_stable_created_at(record).timestamp(),
            -(record.id or 0),
        ),
    )
    tokens = [
        token for token, _ in Counter(_cluster_tokens(ordered_records)).most_common(3)
    ]
    prefix = _SUMMARY_PREFIX.get(lead.category or "learning", "经验聚类")
    lead_text = _strip_anchor_redundancy(
        _normalize_summary_text(lead.wisdom_text or ""),
        anchor=anchor,
    )
    if len(ordered_records) > 1:
        token_hint = "、".join(tokens[:2]) if tokens else "相近主题"
        return f"{prefix}：聚合了{len(ordered_records)}条相近经验，围绕{token_hint}，核心结论是{lead_text}"
    return f"{prefix}：{lead_text}"


def _resolve_cluster_z(
    cluster_records: Sequence[SemanticWisdom],
    distiller: Optional[MemoryDistiller] = None,
) -> float:
    ordered_records = _sort_cluster_records(cluster_records)
    helper = distiller or MemoryDistiller()
    category = ordered_records[0].category or "learning"
    return helper.get_z_for_category(category)


def _assign_cluster_positions(nodes: List[Dict[str, Any]]) -> None:
    orbit_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for index, node in enumerate(nodes):
        orbit_buckets[node["_category"]].append(
            {
                "index": index,
                "cluster_id": node["id"],
                "z": node["z"],
            }
        )

    for entries in orbit_buckets.values():
        if not entries:
            continue
        radius = _radius_from_z(entries[0]["z"])
        angle_map = _deconflict_orbit_angles(entries, radius)
        for entry in entries:
            x, y = _polar_to_cartesian(radius, angle_map[entry["index"]])
            nodes[entry["index"]]["x"] = x
            nodes[entry["index"]]["y"] = y


def _deconflict_orbit_angles(
    entries: Sequence[Dict[str, Any]], radius: float
) -> Dict[int, float]:
    if not entries:
        return {}

    min_angle = _minimum_orbital_angle(radius)
    ordered = sorted(
        (
            {
                **entry,
                "base_angle": _stable_cluster_angle(entry["cluster_id"]),
            }
            for entry in entries
        ),
        key=lambda entry: (entry["base_angle"], entry["cluster_id"]),
    )
    if len(ordered) == 1:
        return {
            ordered[0]["index"]: _advance_angle_to_safe_band(
                ordered[0]["base_angle"], radius
            )
        }

    linearized = _linearize_orbit_entries(ordered)
    proposed: Dict[int, float] = {}
    group_start = 0
    for position in range(1, len(linearized) + 1):
        reached_end = position == len(linearized)
        if (
            not reached_end
            and linearized[position]["linear_angle"]
            - linearized[position - 1]["linear_angle"]
            >= min_angle
        ):
            continue
        group = linearized[group_start:position]
        if len(group) == 1:
            proposed[group[0]["index"]] = group[0]["linear_angle"]
        else:
            group_center = sum(item["linear_angle"] for item in group) / len(group)
            start_angle = group_center - min_angle * (len(group) - 1) / 2.0
            for offset, item in enumerate(
                sorted(group, key=lambda candidate: candidate["cluster_id"])
            ):
                proposed[item["index"]] = start_angle + offset * min_angle
        group_start = position

    resolved: Dict[int, float] = {}
    previous_angle: Optional[float] = None
    for item in linearized:
        angle = _advance_angle_to_safe_band(proposed[item["index"]], radius)
        if previous_angle is not None and angle - previous_angle < min_angle:
            angle = _advance_angle_to_safe_band(previous_angle + min_angle, radius)
        resolved[item["index"]] = angle % 360.0
        previous_angle = angle
    return resolved


def _linearize_orbit_entries(entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(entries) <= 1:
        return [dict(entry, linear_angle=entry["base_angle"]) for entry in entries]

    gaps = []
    for index, entry in enumerate(entries):
        next_entry = entries[(index + 1) % len(entries)]
        next_angle = next_entry["base_angle"] + (
            360.0 if index == len(entries) - 1 else 0.0
        )
        gaps.append((next_angle - entry["base_angle"], index))
    _, largest_gap_index = max(gaps, key=lambda item: (item[0], item[1]))

    linearized: List[Dict[str, Any]] = []
    for offset in range(len(entries)):
        source_index = (largest_gap_index + 1 + offset) % len(entries)
        source = dict(entries[source_index])
        linear_angle = source["base_angle"]
        if source_index <= largest_gap_index:
            linear_angle += 360.0
        source["linear_angle"] = linear_angle
        linearized.append(source)
    return linearized


def _stable_cluster_angle(cluster_id: str) -> float:
    digest = hashlib.sha256(cluster_id.encode("utf-8")).hexdigest()
    whole_degrees = int(digest[:16], 16) % 360
    fractional = int(digest[16:24], 16) / float(16**8)
    return (whole_degrees + fractional) % 360.0


def _advance_angle_to_safe_band(angle_degrees: float, radius: float) -> float:
    normalized = angle_degrees
    if radius < math.sqrt(2) or radius <= _MIN_COORDINATE_MAGNITUDE:
        return normalized

    minimum_local_angle = math.degrees(math.asin(_MIN_COORDINATE_MAGNITUDE / radius))
    maximum_local_angle = 90.0 - minimum_local_angle
    while True:
        local_angle = normalized % 90.0
        if minimum_local_angle <= local_angle <= maximum_local_angle:
            return normalized
        if local_angle < minimum_local_angle:
            return normalized + (minimum_local_angle - local_angle)
        normalized += 90.0 - local_angle + minimum_local_angle


def _minimum_orbital_angle(radius: float) -> float:
    return max(
        _ORBITAL_JITTER_TRIGGER_DEGREES,
        math.degrees(_MIN_ORBITAL_ARC_LENGTH / max(radius, 1e-6)),
    )


def _radius_from_z(z: float) -> float:
    rounded_z = round(z, 1)
    if rounded_z in _POLAR_RADIUS_BY_Z:
        return _POLAR_RADIUS_BY_Z[rounded_z]
    return round(max(1.6, min(5.0, rounded_z)), 6)


def _polar_to_cartesian(radius: float, angle_degrees: float) -> Tuple[float, float]:
    theta = math.radians(angle_degrees % 360.0)
    return round(radius * math.cos(theta), 6), round(radius * math.sin(theta), 6)


def _aggregate_cluster_importance(cluster_records: Sequence[SemanticWisdom]) -> float:
    values = [max(record.importance or 0.0, 0.0) for record in cluster_records]
    if not values:
        return 0.1
    peak = max(values)
    mean = sum(values) / len(values)
    return round(0.6 * peak + 0.4 * mean, 6)


def _cluster_tokens(cluster_records: Sequence[SemanticWisdom]) -> List[str]:
    tokens: List[str] = []
    for record in _sort_cluster_records(cluster_records):
        tokens.extend(_extract_topic_tokens(record.wisdom_text or ""))
    return tokens


def _match_anchor_lexicon(category: str, token_set: set[str]) -> Optional[str]:
    lexicon: List[Tuple[Tuple[str, ...], str]] = []
    if category == "learning":
        lexicon.extend(_AI_ANCHOR_LEXICON)
    if category == "hardware":
        lexicon.extend(_HARDWARE_ANCHOR_LEXICON)
    for keywords, anchor in lexicon:
        if all(keyword in token_set for keyword in keywords):
            return anchor
    return None


def _match_culture_anchor(token_set: set[str]) -> Optional[str]:
    for keywords, anchor in _CULTURE_ANCHOR_LEXICON:
        if all(keyword in token_set for keyword in keywords):
            return anchor
    return None


def _extract_lead_anchor_keyword(text: str) -> Optional[str]:
    ordered_tokens = _ordered_topic_tokens(text)
    preferred_tokens = [
        token
        for token in ordered_tokens
        if _is_semantic_anchor_token(token) and token not in _GENERIC_ANCHOR_TOKENS
    ]
    for token in preferred_tokens:
        if not _contains_connector(token):
            return _normalize_anchor_token(token)
    for token in ordered_tokens:
        if (
            not _contains_connector(token)
            and len(token.strip()) >= 2
            and token not in _GENERIC_ANCHOR_TOKENS
        ):
            return _normalize_anchor_token(token.strip())
    return None


def _ordered_topic_tokens(text: str) -> List[str]:
    cleaned = re.sub(
        r"[，。！？；：、,.!?;:()（）\[\]{}\-_/\\]+", " ", (text or "").strip().lower()
    )
    chunks = [
        chunk
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", cleaned)
        if chunk not in _STOPWORDS
    ]
    ordered: List[str] = []
    for chunk in chunks:
        normalized = chunk.strip()
        if normalized and normalized not in _STOPWORDS:
            ordered.append(normalized)
    return ordered


def _is_semantic_anchor_token(token: str) -> bool:
    stripped = (token or "").strip()
    if len(stripped) < 2:
        return False
    if _contains_connector(stripped):
        return False
    if re.fullmatch(r"[a-z0-9]{2,}", stripped):
        return stripped in {"ai"}
    return True


def _normalize_anchor_token(token: str) -> str:
    stripped = (token or "").strip()
    if not stripped:
        return stripped
    if stripped in _GENERIC_ANCHOR_TOKENS:
        return ""
    if re.fullmatch(r"[a-z0-9]{2,}", stripped):
        return stripped[:4]
    if len(stripped) <= 4:
        return stripped
    parts = [
        part
        for part in re.split(r"[，。！？；：、,.!?;:()（）\[\]{}\-_/\\\s]+", stripped)
        if part
    ]
    if len(parts) >= 2 and len(parts[0]) >= 2 and len(parts[1]) >= 2:
        return parts[0][:2] + parts[1][:2]
    if len(stripped) >= 4:
        return stripped[:4]
    return stripped


def _contains_connector(token: str) -> bool:
    return any(connector in (token or "") for connector in _CONNECTOR_TOKENS)


def _build_cluster_id(category: str, contains: Sequence[int]) -> str:
    normalized_ids = [int(wisdom_id) for wisdom_id in sorted(contains)]
    digest = hashlib.sha1(
        ",".join(str(wisdom_id) for wisdom_id in normalized_ids).encode("utf-8")
    ).hexdigest()[:12]
    return f"cluster:{category}:{len(normalized_ids)}:{digest}"


def _sort_cluster_records(
    cluster_records: Sequence[SemanticWisdom],
) -> List[SemanticWisdom]:
    return sorted(
        cluster_records,
        key=lambda record: (
            -(record.importance or 0.0),
            _stable_created_at(record),
            record.id or 0,
        ),
    )


def _stable_created_at(record: SemanticWisdom) -> datetime:
    return record.created_at or datetime(1970, 1, 1)


def _normalize_summary_text(text: str) -> str:
    cleaned = re.sub(r"\s+", "", (text or "").strip())
    cleaned = _collapse_repeated_clauses(cleaned)
    if len(cleaned) > 24:
        cleaned = cleaned[:23].rstrip("，；。,. ") + "。"
    elif cleaned and cleaned[-1] not in "。；!?！？":
        cleaned += "。"
    return cleaned or "经验需要继续沉淀。"


def _collapse_repeated_clauses(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""
    parts = [item.strip() for item in re.split(r"[。！？]", normalized) if item.strip()]
    deduped_parts: List[str] = []
    seen: set[str] = set()
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        deduped_parts.append(part)
    if not deduped_parts:
        return normalized
    return "。".join(deduped_parts) + "。"


def _strip_anchor_redundancy(text: str, *, anchor: str) -> str:
    cleaned = (text or "").strip()
    normalized_anchor = (anchor or "").strip()
    if not cleaned or not normalized_anchor:
        return cleaned
    for marker in ("，", "：", ":", " "):
        prefix = f"{normalized_anchor}{marker}"
        if cleaned.startswith(prefix):
            trimmed = cleaned[len(prefix) :].strip()
            if trimmed:
                return trimmed
    return cleaned


def _module_name_from_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return ".".join((root.name, *relative.parts))


def _resolve_relative_module(source_module: str, module: str, level: int) -> str:
    parts = source_module.split(".")
    anchor = parts[:-level] if level <= len(parts) else []
    if module:
        anchor.extend(module.split("."))
    return ".".join(part for part in anchor if part)


def _should_include_import(module_name: str) -> bool:
    return bool(module_name) and (
        module_name == "src" or module_name.startswith("src.")
    )


def _split_git_line(line: str) -> tuple[str, str, str]:
    parts = line.split("\x1f")
    if len(parts) != 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def _path_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _sensor_reading_to_payload(reading: SensorReading) -> Dict[str, Any]:
    details = _to_jsonable(reading.details)
    pressure_signals = _to_jsonable(reading.pressure_signals)
    return {
        "cpu_percent": details.get("cpu_percent"),
        "memory_percent": details.get("memory_percent"),
        "host_resource_pressure": pressure_signals.get("host_resource_pressure"),
        "health_score": reading.health_score,
        "status": reading.status,
        "confidence": reading.confidence,
        "source": reading.source,
        "details": details,
    }


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _write_json_atomic(destination: Path, payload: Dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f"{destination.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(destination)


if __name__ == "__main__":
    raise SystemExit(main())
