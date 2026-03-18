"""Agent主循环模块"""

import asyncio
from collections import defaultdict
import random
import signal
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.constants import MEMORY_DB_PATH
from config.settings import settings
from src.brain import CentralBrain
from src.decision import ActionIntent, DecisionBrain, DecisionOutcome
from src.language import LanguageMask
from src.learning import LearningDistiller, ParsedNews
from src.learning.culture_seed_bank import DEFAULT_CULTURE_SEEDS, CultureSeedEntry
from src.skills import WebExplorer
from src.treasury import TradeExecutor
from src.utils.integrity import IntegrityExpectation, IntegrityManager
from src.utils.logger import log
from src.utils.map_exporter import export_evolution_map


class AutonomousLifeLoop:
    """基于心跳的自主巡航闭环。"""

    TARGET_CAPACITY = {
        "finance": 3,
        "learning": 3,
        "culture": 9,
    }

    def __init__(
        self,
        brain: CentralBrain,
        repo_root: Optional[str] = None,
        decision_brain: Optional[DecisionBrain] = None,
        learning_distiller: Optional[LearningDistiller] = None,
        trade_executor: Optional[TradeExecutor] = None,
        heartbeat_seconds: int = 15 * 60,
        shadow_commit_enabled: bool = True,
        map_path: Optional[str] = None,
        manifesto_path: Optional[str] = None,
    ):
        self.brain = brain
        self.repo_root = Path(repo_root or settings.BASE_DIR)
        self.map_path = (
            Path(map_path) if map_path else self.repo_root / "evolution_map.json"
        )
        self.manifesto_path = (
            Path(manifesto_path)
            if manifesto_path
            else self.repo_root / "decision_manifesto.md"
        )
        self.decision_brain = decision_brain or DecisionBrain(
            map_path=str(self.map_path)
        )
        self.learning_distiller = learning_distiller or LearningDistiller(
            memory_manager=self.brain.memory
        )
        self.learning_distiller.memory_manager = self.brain.memory
        self.learning_distiller.decision_brain = self.decision_brain
        if trade_executor is None:
            self.trade_executor = TradeExecutor(
                self.brain.treasury,
                memory_manager=self.brain.memory,
            )
        else:
            self.trade_executor = trade_executor
        self.trade_executor.decision_brain = self.decision_brain
        self.web_explorer = WebExplorer(
            memory_manager=self.brain.memory,
            vector_retriever=self.brain.memory.vector_retriever,
            decision_brain=self.decision_brain,
            map_path=str(self.map_path),
        )
        self.heartbeat_seconds = heartbeat_seconds
        self.shadow_commit_enabled = shadow_commit_enabled
        self.integrity_manager = IntegrityManager(repo_path=str(self.repo_root))
        self.heartbeat_count = 0
        self.cruise_log: List[Dict[str, Any]] = []
        self.intent_counters: Dict[str, int] = defaultdict(int)
        self.outcome_counters: Dict[str, int] = defaultdict(int)
        self._culture_foundation_seeded = False
        self._boot_semantic_commit_done = False

    def register_with_chronos(self) -> None:
        self.brain.chronos.schedule_heartbeat_task(
            self.run_heartbeat,
            interval_seconds=self.heartbeat_seconds,
            job_id="autonomous_heartbeat",
        )

    async def ensure_cultural_foundation(
        self, export_map: bool = True
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._ensure_cultural_foundation_sync,
            export_map,
        )

    def _ensure_cultural_foundation_sync(
        self, export_map: bool = True
    ) -> List[Dict[str, Any]]:
        if self._culture_foundation_seeded:
            return []
        self._culture_foundation_seeded = True

        results = self.learning_distiller.ingest_culture_batch(
            seed_entries=DEFAULT_CULTURE_SEEDS,
            export_map=False,
            repo_root=str(self.repo_root),
        )
        created_wisdom_ids = self._collect_created_wisdom_ids(results)
        if export_map and created_wisdom_ids:
            self._sync_evolution_snapshot("culture_foundation", created_wisdom_ids)
        return results

    async def run_heartbeat(self, defer_execution: bool = False) -> Dict[str, Any]:
        return await asyncio.to_thread(self._run_heartbeat_sync, defer_execution)

    def _run_heartbeat_sync(self, defer_execution: bool = False) -> Dict[str, Any]:
        self.heartbeat_count += 1
        self.decision_brain.reload()
        intent = self.generate_spontaneous_intent()
        self.intent_counters[
            str(intent.metadata.get("template", intent.strategy_name))
        ] += 1

        outcome = self.decision_brain.evaluate_intent(intent)
        if not outcome.allowed:
            if defer_execution:
                event = {
                    "heartbeat": self.heartbeat_count,
                    "domain": intent.normalized_domain,
                    "strategy": intent.strategy_name,
                    "template": intent.metadata.get("template"),
                    "allowed": False,
                    "decision": outcome.to_dict(),
                    "execution_deferred": True,
                    "deferred_action": "intercept",
                    "intent_payload": self._serialize_intent(intent),
                }
                self.cruise_log.append(event)
                return event
            self.outcome_counters["intercepted"] += 1
            intercept_result = self._record_interception(intent, outcome)
            event = {
                "heartbeat": self.heartbeat_count,
                "domain": intent.normalized_domain,
                "strategy": intent.strategy_name,
                "template": intent.metadata.get("template"),
                "allowed": False,
                "decision": outcome.to_dict(),
                "intercept": intercept_result,
            }
            if self.heartbeat_count == 1 and not self._boot_semantic_commit_done:
                event["boot_commit"] = self._commit_bootstrap_local_vector_shift()
            self.cruise_log.append(event)
            return event

        if defer_execution:
            result = {
                "heartbeat": self.heartbeat_count,
                "domain": intent.normalized_domain,
                "strategy": intent.strategy_name,
                "template": intent.metadata.get("template"),
                "allowed": True,
                "decision": outcome.to_dict(),
                "execution_deferred": True,
                "deferred_action": "execute",
                "intent_payload": self._serialize_intent(intent),
            }
            self.cruise_log.append(result)
            return result

        execution_result = self.execute_intent(intent, outcome)
        created_wisdom_ids = self._collect_created_wisdom_ids(execution_result)
        sync_result = None
        if created_wisdom_ids:
            sync_result = self._sync_evolution_snapshot(
                reason=str(intent.metadata.get("template", intent.strategy_name)),
                created_wisdom_ids=created_wisdom_ids,
            )

        result = {
            "heartbeat": self.heartbeat_count,
            "domain": intent.normalized_domain,
            "strategy": intent.strategy_name,
            "template": intent.metadata.get("template"),
            "allowed": True,
            "decision": outcome.to_dict(),
            "execution": execution_result,
            "sync": sync_result,
        }
        if self.heartbeat_count == 1 and not self._boot_semantic_commit_done:
            result["boot_commit"] = self._commit_bootstrap_local_vector_shift()
        self.outcome_counters["executed"] += 1
        self.cruise_log.append(result)
        return result

    def execute_deferred_heartbeat_action(
        self, heartbeat_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        intent = self._deserialize_intent(heartbeat_result.get("intent_payload", {}))
        outcome = self._deserialize_outcome(heartbeat_result.get("decision", {}))
        if not outcome.allowed:
            self.outcome_counters["intercepted"] += 1
            intercept_result = self._record_interception(intent, outcome)
            return {
                "status": "completed",
                "kind": "intercept",
                "heartbeat": heartbeat_result.get("heartbeat"),
                "intercept": intercept_result,
            }

        execution_result = self.execute_intent(intent, outcome)
        created_wisdom_ids = self._collect_created_wisdom_ids(execution_result)
        sync_result = None
        if created_wisdom_ids:
            sync_result = self._sync_evolution_snapshot(
                reason=str(intent.metadata.get("template", intent.strategy_name)),
                created_wisdom_ids=created_wisdom_ids,
            )
        self.outcome_counters["executed"] += 1
        return {
            "status": "completed",
            "kind": "execute",
            "heartbeat": heartbeat_result.get("heartbeat"),
            "execution": execution_result,
            "sync": sync_result,
        }

    async def run_accelerated(
        self,
        hours: int = 48,
        heartbeat_minutes: int = 30,
    ) -> List[Dict[str, Any]]:
        total_heartbeats = max(1, int(hours * 60 / max(heartbeat_minutes, 1)))
        results = []
        for _ in range(total_heartbeats):
            results.append(await self.run_heartbeat())
        return results

    def get_cruise_statistics(self) -> Dict[str, Any]:
        return {
            "heartbeats": self.heartbeat_count,
            "intent_counters": dict(self.intent_counters),
            "outcome_counters": dict(self.outcome_counters),
            "recent_logs": self.cruise_log[-5:],
        }

    def generate_spontaneous_intent(self) -> ActionIntent:
        scores = self._calculate_domain_scores()
        domain = self._select_domain(scores)
        wuwei_weight = self.decision_brain.get_cultural_directive_weight("无为")
        zelin_weight = self.decision_brain.get_cultural_directive_weight("择邻处")

        if domain == "learning":
            if zelin_weight > 0.03 and self.heartbeat_count % 5 == 0:
                return ActionIntent(
                    domain="learning",
                    intent_text="从低信誉论坛批量抓取十几篇资料并直接沉淀结论。",
                    strategy_name="low_trust_scrape",
                    estimated_steps=8,
                    energy_cost=0.92,
                    tags=("学习", "抓取", "论坛"),
                    metadata={
                        "template": "trivial_scrape",
                        "source_reputation": 0.24,
                        "source_label": "forum.lowtrust",
                    },
                )
            return ActionIntent(
                domain="learning",
                intent_text="从高信誉技术源提炼一条可交付、可验证的学习结论。",
                strategy_name="high_value_extract",
                estimated_steps=2,
                energy_cost=0.28,
                tags=("学习", "提炼", "交付"),
                metadata={
                    "template": "high_value_extract",
                    "source_reputation": 0.93,
                    "source_label": "curated.digest",
                },
            )

        if domain == "culture":
            if wuwei_weight > 0.03 and self.heartbeat_count % 6 == 0:
                return ActionIntent(
                    domain="culture",
                    intent_text="把多个文化站点全量抓下后再做一份宽泛大综述。",
                    strategy_name="wide_culture_sweep",
                    estimated_steps=9,
                    energy_cost=0.95,
                    tags=("文化", "高能耗", "综述"),
                    metadata={
                        "template": "wide_culture_sweep",
                        "source_reputation": 0.94,
                        "source_label": "canon.bundle",
                    },
                )
            return ActionIntent(
                domain="culture",
                intent_text="从文化经典中提炼一条可以进入决策基因层的原则。",
                strategy_name="culture_seed_extract",
                estimated_steps=3,
                energy_cost=0.22,
                tags=("文化", "经典", "提炼"),
                metadata={
                    "template": "culture_seed_extract",
                    "source_reputation": 0.99,
                    "source_label": "canon.seed",
                },
            )

        if self.heartbeat_count % 7 == 0:
            return ActionIntent(
                domain="treasury",
                intent_text="尝试激进追涨并快速翻本。",
                strategy_name="aggressive_probe",
                amount=120.0,
                volatility=0.82,
                expected_profit=36.0,
                estimated_steps=2,
                energy_cost=0.7,
                tags=("交易", "高风险", "翻本"),
                metadata={"template": "risk_probe"},
            )
        return ActionIntent(
            domain="treasury",
            intent_text="审查账本稳定性并验证风险敞口是否需要收缩。",
            strategy_name="capital_guard_review",
            amount=20.0,
            volatility=0.15,
            expected_profit=2.0,
            estimated_steps=2,
            energy_cost=0.18,
            tags=("交易", "审查", "保守"),
            metadata={"template": "capital_guard_review"},
        )

    def execute_intent(
        self,
        intent: ActionIntent,
        outcome: DecisionOutcome,
    ) -> Dict[str, Any]:
        if (
            intent.normalized_domain == "finance"
            or intent.normalized_domain == "treasury"
        ):
            return self._execute_finance_intent(intent, outcome)
        if intent.normalized_domain == "culture":
            return self._execute_culture_intent(intent)
        return self._execute_learning_intent(intent)

    def _execute_learning_intent(
        self,
        intent: ActionIntent,
    ) -> Dict[str, Any]:
        template = str(intent.metadata.get("template", "high_value_extract"))
        parsed = self._build_autonomous_learning_sample(intent, template)
        result = self.learning_distiller.ingest_parsed_news(
            parsed,
            export_map=False,
            repo_root=str(self.repo_root),
            trigger_type="autonomous_learning",
        )
        return {"kind": "learning", "result": result}

    def _execute_culture_intent(self, intent: ActionIntent) -> Dict[str, Any]:
        template = str(intent.metadata.get("template", "culture_seed_extract"))
        if template == "culture_seed_extract":
            cluster_keys = sorted(
                {entry.cluster_key for entry in DEFAULT_CULTURE_SEEDS}
            )
            target_key = cluster_keys[self.heartbeat_count % len(cluster_keys)]
            selected = [
                entry
                for entry in DEFAULT_CULTURE_SEEDS
                if entry.cluster_key == target_key
            ]
            result = self.learning_distiller.ingest_culture_batch(
                seed_entries=selected,
                export_map=False,
                repo_root=str(self.repo_root),
            )
            return {"kind": "culture", "result": result, "cluster_key": target_key}

        parsed = self._build_autonomous_culture_sample(intent)
        result = self.learning_distiller.ingest_parsed_news(
            parsed,
            export_map=False,
            repo_root=str(self.repo_root),
            trigger_type="autonomous_culture",
        )
        return {"kind": "culture", "result": result}

    def _execute_finance_intent(
        self,
        intent: ActionIntent,
        outcome: DecisionOutcome,
    ) -> Dict[str, Any]:
        if str(intent.metadata.get("template")) == "risk_probe":
            result = self.trade_executor.execute_trade(
                amount=intent.amount or 20.0,
                strategy_name=intent.strategy_name,
                execute_callback=lambda amount, **_: {"success": False, "profit": 0.0},
            )
            return {"kind": "finance", "result": result}

        memory_id = self.brain.memory.create_memory(
            event="自主巡航财务审查：检查账本稳定性与风险敞口。",
            thought="在冲动扩张前先复核资金边界。",
            lesson="知足不辱，先守住现金与节奏。",
            importance=0.72,
            source_type="system",
            verification_status="auto",
            raw_payload={"template": intent.metadata.get("template")},
        )
        return {
            "kind": "finance",
            "result": {
                "memory_ids": [int(memory_id)] if memory_id is not None else [],
                "decision": outcome.to_dict(),
            },
        }

    def _record_interception(
        self,
        intent: ActionIntent,
        outcome: DecisionOutcome,
    ) -> Dict[str, Any]:
        log.warning(
            "🧬 自主巡航拦截 | "
            f"heartbeat={self.heartbeat_count} | domain={intent.normalized_domain} | "
            f"gene={outcome.matched_gene} | action={outcome.action} | reasons={list(outcome.reasons)}"
        )

        source_memories = []
        snapshots = [
            (
                f"自主巡航拦截：{intent.strategy_name}",
                f"心跳 {self.heartbeat_count} 生成了 {intent.intent_text}",
                self._interception_lesson(outcome),
            ),
            (
                f"基因阻断：{outcome.matched_gene or 'unknown'}",
                "；".join(outcome.reasons),
                self._interception_lesson(outcome),
            ),
            (
                f"自主巡航复盘：{intent.strategy_name}",
                f"系统对 {intent.normalized_domain} 动作执行了 {outcome.action}",
                self._interception_lesson(outcome),
            ),
        ]
        for event, thought, lesson in snapshots:
            memory_id = self.brain.memory.create_memory(
                event=event,
                thought=thought,
                lesson=lesson,
                importance=0.86,
                source_type="system",
                verification_status="auto",
                raw_payload={
                    "strategy_name": intent.strategy_name,
                    "matched_gene": outcome.matched_gene,
                    "action": outcome.action,
                },
            )
            if memory_id is None:
                continue
            memory = self.brain.memory.db_manager.get_memory_by_id(int(memory_id))
            if memory is not None:
                source_memories.append(memory)

        distill_result = self.brain.memory.distill_memory(
            source_memories=source_memories,
            trigger_type="autonomous_intercept",
            generator=lambda context: self._interception_lesson(outcome),
        )
        created_wisdom_ids = self._collect_created_wisdom_ids(distill_result)
        sync_result = None
        if created_wisdom_ids:
            sync_result = self._sync_evolution_snapshot(
                reason="autonomous_intercept",
                created_wisdom_ids=created_wisdom_ids,
            )

        return {"distillation": distill_result, "sync": sync_result}

    def _sync_evolution_snapshot(
        self,
        reason: str,
        created_wisdom_ids: List[int],
    ) -> Dict[str, Any]:
        shadow_commit = self._shadow_commit_if_needed(created_wisdom_ids)
        payload = export_evolution_map(
            self.brain.memory,
            output_path=str(self.map_path),
            repo_root=str(self.repo_root),
            brain=self.brain,
        )
        self.decision_brain.reload()
        manifesto_path = self.decision_brain.generate_manifesto(
            output_path=str(self.manifesto_path)
        )
        return {
            "reason": reason,
            "wisdom_ids": created_wisdom_ids,
            "shadow_commit": shadow_commit,
            "map_path": str(self.map_path),
            "manifesto_path": str(manifesto_path),
            "wisdom_node_count": len(payload.get("wisdom_nodes", [])),
            "boot_commit": self._commit_bootstrap_local_vector_shift(),
        }

    @staticmethod
    def _serialize_intent(intent: ActionIntent) -> Dict[str, Any]:
        return {
            "domain": intent.domain,
            "intent_text": intent.intent_text,
            "strategy_name": intent.strategy_name,
            "amount": intent.amount,
            "volatility": intent.volatility,
            "expected_profit": intent.expected_profit,
            "estimated_steps": intent.estimated_steps,
            "energy_cost": intent.energy_cost,
            "tags": list(intent.tags),
            "metadata": dict(intent.metadata),
        }

    @staticmethod
    def _deserialize_intent(payload: Dict[str, Any]) -> ActionIntent:
        return ActionIntent(
            domain=str(payload.get("domain") or "learning"),
            intent_text=str(payload.get("intent_text") or ""),
            strategy_name=str(payload.get("strategy_name") or ""),
            amount=float(payload.get("amount") or 0.0),
            volatility=float(payload.get("volatility") or 0.0),
            expected_profit=float(payload.get("expected_profit") or 0.0),
            estimated_steps=int(payload.get("estimated_steps") or 1),
            energy_cost=float(payload.get("energy_cost") or 0.0),
            tags=tuple(payload.get("tags") or []),
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _deserialize_outcome(payload: Dict[str, Any]) -> DecisionOutcome:
        return DecisionOutcome(
            allowed=bool(payload.get("allowed")),
            execution_probability=float(payload.get("execution_probability") or 0.0),
            action=str(payload.get("action") or "allow"),
            matched_gene=payload.get("matched_gene"),
            matched_cluster_id=payload.get("matched_cluster_id"),
            similarity=float(payload.get("similarity") or 0.0),
            reasons=tuple(payload.get("reasons") or []),
            simplified_plan=tuple(payload.get("simplified_plan") or []),
            gene_weight=float(payload.get("gene_weight") or 0.0),
        )

    def _shadow_commit_if_needed(self, created_wisdom_ids: List[int]) -> Dict[str, Any]:
        if not self.shadow_commit_enabled or not created_wisdom_ids:
            return {"committed": False, "reason": "disabled_or_empty"}

        db_relative_path = MEMORY_DB_PATH.replace("\\", "/")
        db_path = self.repo_root / db_relative_path
        if not db_path.exists():
            return {"committed": False, "reason": "memory_db_missing"}

        add_result = self.integrity_manager.run_git_command(
            ["add", "--", db_relative_path]
        )
        if add_result.exit_code != 0:
            return {
                "committed": False,
                "reason": "git_add_failed",
                "stderr": add_result.stderr,
            }

        staged_result = self.integrity_manager.run_git_command(
            ["diff", "--cached", "--name-only", "--", db_relative_path]
        )
        if staged_result.exit_code != 0 or db_relative_path not in staged_result.stdout:
            return {"committed": False, "reason": "no_db_change"}

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit_result = self.integrity_manager.run_git_command(
            ["commit", "-m", f"evolution: ABU 认知自主演化 - {timestamp}"]
        )
        return {
            "committed": commit_result.exit_code == 0,
            "reason": "ok" if commit_result.exit_code == 0 else "git_commit_failed",
            "stdout": commit_result.stdout,
            "stderr": commit_result.stderr,
        }

    def _commit_bootstrap_local_vector_shift(self) -> Dict[str, Any]:
        if not self.shadow_commit_enabled:
            return {"committed": False, "reason": "shadow_commit_disabled"}
        if self._boot_semantic_commit_done or self.heartbeat_count != 1:
            return {"committed": False, "reason": "not_first_heartbeat"}

        add_result = self.integrity_manager.run_git_command(
            [
                "add",
                "--",
                "data/memories.db",
                "evolution_map.json",
                "decision_manifesto.md",
            ]
        )
        if add_result.exit_code != 0:
            return {
                "committed": False,
                "reason": "git_add_failed",
                "stderr": add_result.stderr,
            }

        staged_result = self.integrity_manager.run_git_command(
            [
                "diff",
                "--cached",
                "--name-only",
                "--",
                "data/memories.db",
                "evolution_map.json",
                "decision_manifesto.md",
            ]
        )
        if staged_result.exit_code != 0 or not staged_result.stdout.strip():
            return {"committed": False, "reason": "no_semantic_diff"}

        commit_result = self.integrity_manager.run_git_command(
            [
                "commit",
                "-m",
                "chore: 语义空间回归本地向量正轨 (GPU 加速)",
            ]
        )
        self._boot_semantic_commit_done = commit_result.exit_code == 0
        return {
            "committed": commit_result.exit_code == 0,
            "reason": "ok" if commit_result.exit_code == 0 else "git_commit_failed",
            "stdout": commit_result.stdout,
            "stderr": commit_result.stderr,
        }

    def _calculate_domain_scores(self) -> Dict[str, float]:
        genes_by_category: Dict[str, List[Any]] = defaultdict(list)
        for gene in self.decision_brain.genes:
            genes_by_category[gene.category].append(gene)

        gravity_values = {
            category: sum(gene.gravity for gene in genes)
            for category, genes in genes_by_category.items()
        }
        dominant_values = {
            category: max((gene.weight for gene in genes), default=0.0)
            for category, genes in genes_by_category.items()
        }
        max_gravity = max(gravity_values.values(), default=1.0) or 1.0
        max_dominant = max(dominant_values.values(), default=1.0) or 1.0

        scores: Dict[str, float] = {}
        for category in ("finance", "learning", "culture"):
            genes = genes_by_category.get(category, [])
            total_gravity = gravity_values.get(category, 0.0) / max_gravity
            dominant = dominant_values.get(category, 0.0) / max_dominant
            sparsity = 1.0 - min(
                len(genes) / max(self.TARGET_CAPACITY.get(category, 1), 1),
                1.0,
            )
            scores[category] = round(
                0.40 * total_gravity + 0.35 * dominant + 0.25 * sparsity, 6
            )
        return scores

    def _select_domain(self, scores: Dict[str, float]) -> str:
        ordered = sorted(scores.items(), key=lambda item: item[0])
        total = sum(max(score, 0.05) for _, score in ordered) or 1.0
        signature = "|".join(
            f"{gene.cluster_id}:{gene.weight:.3f}" for gene in self.decision_brain.genes
        )
        selector = self._stable_ratio(f"{self.heartbeat_count}|{signature}")

        cursor = 0.0
        for domain, raw_score in ordered:
            cursor += max(raw_score, 0.05) / total
            if selector <= cursor:
                return domain
        return max(scores.items(), key=lambda item: item[1])[0]

    @staticmethod
    def _stable_ratio(seed: str) -> float:
        digest = sum(ord(char) for char in seed)
        return (digest % 1000) / 1000.0

    def _build_autonomous_learning_sample(
        self,
        intent: ActionIntent,
        template: str,
    ) -> ParsedNews:
        if template == "high_value_extract":
            return ParsedNews(
                url="https://curated.example.com/high-value",
                title="高信誉技术周报聚焦交付与自动化",
                summary="维护者总结如何用更少步骤完成高价值交付。",
                entities=["交付", "自动化"],
                causal_signals=["高信誉来源指出减少步骤后交付效率反而更高"],
                event="新闻事实: 高信誉技术源强调以少步骤完成可验证交付。",
                thought="因果分析: 当流程更短且验证更直接时，系统更容易沉淀稳定模式。",
                lesson="泛化经验: 学习动作应优先提炼可复用模式与输出物。",
                importance=0.88,
                category="learning",
                source="curated.example.com",
                fetched_at=datetime.now().isoformat(),
                raw_text=intent.intent_text,
                source_reputation=float(
                    intent.metadata.get("source_reputation", 0.93) or 0.93
                ),
            )
        return ParsedNews(
            url="https://forum.lowtrust/thread",
            title="论坛杂谈：十几种做法一起抓",
            summary="来源混杂且缺乏验证。",
            entities=["论坛"],
            causal_signals=["低信誉样本常混入噪声与误导"],
            event="新闻事实: 低信誉论坛出现大量未经验证的抓取建议。",
            thought="因果分析: 来源信誉不足时，直接吸收会扩大认知噪声。",
            lesson="泛化经验: 先校验来源，再决定是否吸收。",
            importance=0.66,
            category="learning",
            source="forum.lowtrust",
            fetched_at=datetime.now().isoformat(),
            raw_text=intent.intent_text,
            source_reputation=float(
                intent.metadata.get("source_reputation", 0.24) or 0.24
            ),
        )

    def _build_autonomous_culture_sample(self, intent: ActionIntent) -> ParsedNews:
        return ParsedNews(
            url="https://canon.bundle/culture",
            title="文化综述草案",
            summary="尝试一次性堆叠多个文化来源并做大综述。",
            entities=["文化", "综述"],
            causal_signals=["高能耗综述容易稀释真正可迁移的原则"],
            event="新闻事实: 多源文化站点被统一纳入大综述草案。",
            thought="因果分析: 当步骤过长时，文化提炼会被噪声覆盖。",
            lesson="泛化经验: 先提炼单一原则，再决定是否扩写综述。",
            importance=0.82,
            category="culture",
            source="canon.bundle",
            fetched_at=datetime.now().isoformat(),
            raw_text=intent.intent_text,
            source_reputation=float(
                intent.metadata.get("source_reputation", 0.94) or 0.94
            ),
        )

    @staticmethod
    def _interception_lesson(outcome: DecisionOutcome) -> str:
        if outcome.matched_gene == "择邻处":
            return "择邻处先行，低信誉不入脑。"
        if outcome.matched_gene in {"大道至简", "无为"}:
            return "无为重减法，繁案先剪枝。"
        if outcome.matched_gene == "交易未稳":
            return "交易未稳，先查根因。"
        return "基因拦截会把冲动动作转成长期规则。"

    @staticmethod
    def _collect_created_wisdom_ids(payload: Any) -> List[int]:
        wisdom_ids: List[int] = []
        if isinstance(payload, dict):
            distillation = payload.get("distillation")
            if isinstance(distillation, dict) and distillation.get("created", 0):
                wisdom_ids.extend(
                    int(item) for item in distillation.get("wisdom_ids", [])
                )
            execution = payload.get("execution")
            if execution is not None:
                wisdom_ids.extend(
                    AutonomousLifeLoop._collect_created_wisdom_ids(execution)
                )
            result = payload.get("result")
            if result is not None:
                wisdom_ids.extend(
                    AutonomousLifeLoop._collect_created_wisdom_ids(result)
                )
        elif isinstance(payload, list):
            for item in payload:
                wisdom_ids.extend(AutonomousLifeLoop._collect_created_wisdom_ids(item))
        return sorted({wisdom_id for wisdom_id in wisdom_ids})

    async def run_discovery_phase(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._run_discovery_phase_sync)

    def _run_discovery_phase_sync(self) -> Dict[str, Any]:
        discovery = self.web_explorer.run_discovery(top_k=3)
        if discovery.skipped:
            log.warning("🛰️ Discovery Phase 已跳过 | reason={}", discovery.reason)
            return {
                "triggered": False,
                "reason": discovery.reason,
                "queries": discovery.queries,
                "selected_urls": [],
                "created_wisdom_ids": [],
            }

        ingested = []
        for crawl_result in discovery.crawled_results:
            parsed_news = self.learning_distiller.news_parser.parse_crawl_result(
                crawl_result
            )
            result = self.learning_distiller.ingest_parsed_news(
                parsed_news,
                export_map=False,
                repo_root=str(self.repo_root),
                trigger_type="autonomous_discovery",
            )
            ingested.append(result)

        created_wisdom_ids = self._collect_created_wisdom_ids(ingested)
        sync_result = None
        if created_wisdom_ids:
            sync_result = self._sync_evolution_snapshot(
                reason="autonomous_discovery",
                created_wisdom_ids=created_wisdom_ids,
            )

        payload = {
            "triggered": True,
            "queries": discovery.queries,
            "selected_urls": [hit.url for hit in discovery.selected_hits],
            "selected_titles": [hit.title for hit in discovery.selected_hits],
            "created_wisdom_ids": created_wisdom_ids,
            "ingested": ingested,
            "sync": sync_result,
        }
        log.info(
            "🛰️ Discovery Phase 完成 | queries={} | selected={} | created_wisdom_ids={}",
            discovery.queries,
            [hit.url for hit in discovery.selected_hits],
            created_wisdom_ids,
        )
        return payload


class HumanLikeAgent:
    """类人型自主智能体主类"""

    def __init__(self):
        """初始化Agent"""
        self.name = "Abu"
        self.is_running = False
        self.brain = None
        self.autonomous_loop = None
        self.language_mask = LanguageMask()
        self.integrity_manager = IntegrityManager(repo_path=str(settings.BASE_DIR))
        self._map_export_interval_seconds = 10
        self._next_map_export_at = None
        self._setup_signal_handlers()

        log.info(f"🤖 {self.name} Agent 初始化完成")
        log.info(f"📂 项目根目录: {settings.BASE_DIR}")

    def _setup_signal_handlers(self):
        """设置信号处理器"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """处理中断信号"""
        signal_name = signal.Signals(signum).name if signum else "UNKNOWN"
        log.info(f"⚠️ 收到中断信号 {signal_name}，准备停止主循环并进入清理阶段...")
        self.stop()

    def _now(self):
        return datetime.now()

    def _sample_map_export_interval_seconds(self) -> int:
        return random.randint(10, 30)

    def _schedule_next_map_export(self):
        self._map_export_interval_seconds = self._sample_map_export_interval_seconds()
        self._next_map_export_at = self._now() + timedelta(
            seconds=self._map_export_interval_seconds
        )

    def _should_export_map(self) -> bool:
        return (
            self._next_map_export_at is not None
            and self._now() >= self._next_map_export_at
        )

    def _export_runtime_map_snapshot(self):
        if self.brain is None:
            return
        export_evolution_map(
            self.brain.memory,
            output_path=str(settings.BASE_DIR / "evolution_map.json"),
            repo_root=str(settings.BASE_DIR),
            brain=self.brain,
        )
        self._schedule_next_map_export()

    async def initialize(self):
        """异步初始化"""
        log.info("🚀 开始初始化所有模块...")
        self.brain = CentralBrain()
        self.autonomous_loop = AutonomousLifeLoop(
            self.brain, repo_root=str(settings.BASE_DIR)
        )
        await self.autonomous_loop.ensure_cultural_foundation(export_map=True)
        self.autonomous_loop.register_with_chronos()
        self.brain.chronos.start_all_schedules()
        self._schedule_next_map_export()
        log.info(
            f"🧠 初始快照 | balance={self.brain.state.balance:.2f} | anxiety={self.brain.state.anxiety:.2f} | sleep_bias={self.brain.state.sleep_interval_bias:.2f}"
        )
        log.info("✅ 所有模块初始化完成")

    async def run(self):
        """运行主循环"""
        self.is_running = True
        log.info(f"▶️ {self.name} Agent 开始运行")

        try:
            await self.initialize()
            if self.brain is None:
                raise RuntimeError("CentralBrain 初始化失败")

            while self.is_running:
                state = self.brain.update_cognition(last_event="main_loop_tick")
                if self._should_export_map():
                    self._export_runtime_map_snapshot()
                if not self.is_running:
                    break
                log.info(
                    "🔄 主循环快照 | "
                    f"balance_ratio={state.balance_ratio:.2%} | "
                    f"anxiety={state.anxiety:.2f} | "
                    f"time_of_day={state.time_of_day} | "
                    f"sleep_bias={state.sleep_interval_bias:.2f} | "
                    f"failure_streak={state.failure_streak} | "
                    f"task_complexity={state.task_complexity:.2f} | "
                    f"input_intensity={state.input_intensity:.2f} | "
                    f"focus_level={state.focus_level:.2f} | "
                    f"active_goal={state.active_goal_id or 'none'} | "
                    f"switch_delay={state.switch_cost_delay_seconds:.2f}s"
                )
                await asyncio.sleep(1 + state.switch_cost_delay_seconds)

        except asyncio.CancelledError:
            log.warning("⚠️ Agent 主任务被取消，开始清理资源")
            raise
        except KeyboardInterrupt:
            log.warning("⚠️ 捕获到 KeyboardInterrupt，开始清理资源")
        except Exception as e:
            log.exception(f"❌ Agent 运行出错: {e}")
            log.error("🧾 详细堆栈如下:\n" + traceback.format_exc())
        finally:
            try:
                await self.cleanup()
            except Exception as cleanup_error:
                log.exception(f"❌ Agent 清理阶段出错: {cleanup_error}")
                log.error("🧾 清理阶段详细堆栈如下:\n" + traceback.format_exc())
            finally:
                log.info(f"🏁 Agent 运行结束 | is_running={self.is_running}")

    async def cleanup(self):
        """清理资源"""
        log.info("🧹 开始清理资源...")
        self.is_running = False

        if self.brain is not None:
            chronos = self.brain.chronos
            scheduler_running = chronos.time_scheduler.is_running
            background_tasks = chronos.background_task_manager.get_running_tasks()
            log.info(
                "🧪 Chronos 清理前状态 | "
                f"scheduler_running={scheduler_running} | "
                f"background_tasks={background_tasks}"
            )
            chronos.shutdown()
            log.info(
                "🧪 Chronos 清理后状态 | "
                f"scheduler_running={chronos.time_scheduler.is_running} | "
                f"background_tasks={chronos.background_task_manager.get_running_tasks()}"
            )

        log.info("✅ 资源清理完成")

    def stop(self):
        """停止Agent"""
        log.info("⏹️ 停止Agent...")
        self.is_running = False

    def generate_completion_report(
        self,
        claimed_file_changes=None,
        command_results=None,
        expected_timestamp_updates=None,
    ):
        """在输出“任务完成”类陈述前生成完整性报告。"""
        expectation = IntegrityExpectation(
            claimed_file_changes=claimed_file_changes or [],
            expected_timestamp_updates=expected_timestamp_updates or {},
            command_results=command_results or [],
        )
        return self.integrity_manager.generate_report(expectation)

    def has_completion_expectations(
        self,
        claimed_file_changes=None,
        command_results=None,
        expected_timestamp_updates=None,
    ) -> bool:
        """判断当前是否存在需要完整性校验的完成声明。"""
        return bool(
            claimed_file_changes or command_results or expected_timestamp_updates
        )

    def format_completion_statement(
        self,
        claimed_file_changes=None,
        command_results=None,
        expected_timestamp_updates=None,
    ) -> str:
        """根据完整性报告生成诚实的完成陈述。"""
        if not self.has_completion_expectations(
            claimed_file_changes=claimed_file_changes,
            command_results=command_results,
            expected_timestamp_updates=expected_timestamp_updates,
        ):
            return "我已经处理了这次输入，并更新了当前认知状态。"

        report = self.generate_completion_report(
            claimed_file_changes=claimed_file_changes,
            command_results=command_results,
            expected_timestamp_updates=expected_timestamp_updates,
        )
        if report.is_success:
            return "任务完成，且我已经用底层证据核对过结果。"
        return self.integrity_manager.format_truthful_failure(report)

    async def process_input(self, user_input: str) -> str:
        """
        处理用户输入

        Args:
            user_input: 用户输入的文本

        Returns:
            Agent的回复
        """
        log.info(f"💬 收到用户输入: {user_input[:50]}...")

        anxiety = 0.0
        if self.brain is not None:
            self.brain.update_cognition(last_event="user_input", user_input=user_input)
            anxiety = self.brain.state.anxiety

        response = self.format_completion_statement()
        response = self.language_mask.mask_response(response, anxiety=anxiety)
        log.info(f"💬 生成回复: {response[:50]}...")

        return response


async def main():
    """主函数"""
    agent = HumanLikeAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
