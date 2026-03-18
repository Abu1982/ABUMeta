"""模块 12 的极简网页动作封装。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.execution import LeadCaptureTarget, SandboxLeadHarvester


@dataclass(frozen=True)
class WebLeadTask:
    url: str
    keyword: str = ""
    source_name: str = ""


async def bootstrap_and_capture_trade_leads(
    tasks: Iterable[WebLeadTask],
    *,
    output_path: str | Path,
    max_items_per_target: int = 10,
) -> dict:
    harvester = SandboxLeadHarvester()
    provision = await harvester.ensure_crawler_stack()
    capture = harvester.capture_trade_leads_csv(
        [
            LeadCaptureTarget(
                url=item.url,
                keyword=item.keyword,
                source_name=item.source_name,
            )
            for item in tasks
        ],
        output_path=output_path,
        max_items_per_target=max_items_per_target,
    )
    return {"provision": provision, "capture": capture}
