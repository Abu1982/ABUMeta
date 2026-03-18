"""跨 Agent 的安全情报交换协议。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any, Dict, Optional

from src.evolution.heritage import GeneHeritage
from src.execution import ShadowSandbox, ToolProvisioner
from src.observability import get_action_journal
from src.security import LogShredder
from src.utils.logger import log


class MoltbookGateway:
    """负责在受控分身之间交换已脱敏的基因规则。"""

    def __init__(
        self,
        heritage: GeneHeritage,
        security_shredder: Optional[LogShredder] = None,
        sandbox: Optional[ShadowSandbox] = None,
    ):
        self.heritage = heritage
        self.shredder = security_shredder or LogShredder()
        self.sandbox = sandbox
        self.journal = get_action_journal()

    def prepare_exchange_payload(
        self, cloning_package: Dict[str, Any]
    ) -> Dict[str, Any]:
        context = self._reserve_exchange_context(cloning_package)
        payload = self._sanitize_payload(copy.deepcopy(cloning_package))
        raw_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if self._contains_potential_keys(raw_str):
            payload = self._sanitize_payload(payload)
        payload.setdefault("exchange_context", context)
        self.journal.log_event(
            component="MoltbookGateway",
            stage="social_prepare",
            action="prepare_exchange_payload",
            status="success",
            payload={
                "origin": payload.get("metadata", {}).get("origin"),
                "exchange_id": context.get("exchange_id", ""),
            },
            priority="normal",
            context=context,
        )
        return payload

    def verify_exchange_payload(
        self, cloning_package: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload = self.prepare_exchange_payload(cloning_package)
        context = payload.get("exchange_context") or self._reserve_exchange_context(
            payload
        )
        signature_ok = self.heritage.verify_cloning_package(payload)
        allow_list = set(payload.get("tool_allow_list", []))
        allow_list_ok = allow_list.issubset(ToolProvisioner.SAFE_ALLOW_LIST)
        preflight_ok = self._run_local_preflight(payload)
        accepted = signature_ok and allow_list_ok and preflight_ok
        result = {
            "accepted": accepted,
            "signature_ok": signature_ok,
            "allow_list_ok": allow_list_ok,
            "preflight_ok": preflight_ok,
            "payload": payload,
            "trace_context": context,
        }
        self.journal.log_event(
            component="MoltbookGateway",
            stage="social_verify",
            action="verify_exchange_payload",
            status="success" if accepted else "rejected",
            payload={
                "signature_ok": signature_ok,
                "allow_list_ok": allow_list_ok,
                "preflight_ok": preflight_ok,
                "exchange_id": context.get("exchange_id", ""),
            },
            reason="社交规则包未通过安全校验" if not accepted else "",
            priority="critical",
            context=context,
        )
        return result

    def inject_gene_rules(
        self,
        cloning_package: Dict[str, Any],
        *,
        target_map_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        verification = self.verify_exchange_payload(cloning_package)
        context = verification.get("trace_context") or self._reserve_exchange_context(
            cloning_package
        )
        if not verification["accepted"]:
            self.journal.log_event(
                component="MoltbookGateway",
                stage="social_inject",
                action="inject_gene_rules",
                status="rejected",
                payload={"exchange_id": context.get("exchange_id", "")},
                reason="社交规则包未通过安全校验",
                priority="critical",
                context=context,
            )
            return {
                "success": False,
                "verification": verification,
                "error": "社交规则包未通过安全校验",
            }

        payload = verification["payload"]
        target = (
            Path(target_map_path)
            if target_map_path
            else self.heritage.repo_root / "evolution_map.json"
        )
        existing = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        rules = existing.get("shared_gene_rules", [])
        existing_rules = {
            str(item.get("rule", "")) for item in rules if isinstance(item, dict)
        }
        injected = []
        for rule in payload.get("gene_rules", []):
            rule_text = str(rule.get("rule", "") or "")
            if not rule_text or rule_text in existing_rules:
                continue
            entry = {
                "rule": rule_text,
                "source": payload.get("metadata", {}).get("origin"),
                "signature": payload.get("metadata", {}).get("signature"),
                "verified": True,
            }
            rules.append(entry)
            existing_rules.add(rule_text)
            injected.append(entry)

        existing["shared_gene_rules"] = rules
        existing["social_contract_metadata"] = {
            "last_imported_at": self.heritage._now_iso(),
            "last_origin": payload.get("metadata", {}).get("origin"),
            "last_signature": payload.get("metadata", {}).get("signature"),
        }
        target.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(
            "🤝 Moltbook 注入完成 | target={} | injected={}", target, len(injected)
        )
        self.journal.log_event(
            component="MoltbookGateway",
            stage="social_inject",
            action="inject_gene_rules",
            status="success",
            payload={
                "exchange_id": context.get("exchange_id", ""),
                "injected_count": len(injected),
                "target_map_path": str(target),
            },
            priority="critical",
            context=context,
        )
        return {
            "success": True,
            "verification": verification,
            "injected_rules": injected,
            "target_map_path": str(target),
            "trace_context": context,
        }

    def _run_local_preflight(self, payload: Dict[str, Any]) -> bool:
        if self.sandbox is None:
            return True
        image = str(payload.get("preferred_sandbox_image") or self.sandbox.image)
        try:
            sandbox = ShadowSandbox(image=image, auto_pull=False)
            context = payload.get("exchange_context") or self._reserve_exchange_context(
                payload
            )
            result = sandbox.execute_shadow_task(
                "print('moltbook_preflight_ok')", timeout=20, trace_context=context
            )
            return bool(result.get("success")) and "moltbook_preflight_ok" in str(
                result.get("stdout", "")
            )
        except Exception:
            return False

    def _reserve_exchange_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = payload.get("metadata") or {}
        parent_trace_id = str(metadata.get("source_trace_id") or "")
        exchange_id = str(
            payload.get("exchange_context", {}).get("exchange_id")
            or self.journal.new_exchange_id()
        )
        return self.journal.reserve_event_context(
            parent_trace_id=parent_trace_id,
            exchange_id=exchange_id,
        )

    def _sanitize_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            return {
                key: self._sanitize_payload(value) for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self._sanitize_payload(item) for item in payload]
        if isinstance(payload, str):
            return self.shredder.sanitize_text(payload)
        return payload

    def _contains_potential_keys(self, text: str) -> bool:
        patterns = (
            r"sk-[A-Za-z0-9_-]{20,}",
            r"Bearer\s+[A-Za-z0-9._-]{16,}",
            r"passwd=[^&\s]+",
            r"password=[^&\s]+",
        )
        return any(
            re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns
        )
