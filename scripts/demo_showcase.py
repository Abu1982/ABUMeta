"""开源演示版快速展示脚本。"""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.decision import ActionIntent, DecisionBrain
from src.execution import PageFetcher
from src.psyche import PsycheEngine
from src.treasury import TreasuryManager


def main() -> None:
    print("ABUMeta 开源演示开始")

    treasury = TreasuryManager()
    treasury_stats = treasury.get_statistics()
    print("\n[金库统计]")
    print(treasury_stats)

    psyche = PsycheEngine()
    psyche_state = psyche.get_current_state()
    print("\n[心理状态]")
    print(
        {
            "anxiety": psyche_state.get("anxiety"),
            "summary": psyche.get_psychological_summary(),
        }
    )

    brain = DecisionBrain(map_path=str(REPO_ROOT / "evolution_map.json"))
    outcome = brain.evaluate_intent(
        ActionIntent(
            domain="learning",
            intent_text="尝试以最少步骤完成一次公开网页信息整理",
            strategy_name="demo_scan",
            estimated_steps=2,
            energy_cost=0.1,
            tags=("演示", "公开网页"),
        )
    )
    print("\n[决策引擎]")
    print(outcome.to_dict())

    fetcher = PageFetcher()
    analysis = fetcher.analyze_access(
        url="https://example.com/",
        status_code=200,
        html="<html><head><title>Example</title></head><body><h1>Example Domain</h1><p>This is a demo page.</p></body></html>",
    )
    print("\n[页面分析]")
    print(
        {
            "title": analysis.get("title"),
            "page_kind": analysis.get("page_kind"),
            "block_kind": analysis.get("block_kind"),
            "strategy_hints": analysis.get("strategy_hints"),
        }
    )

    print("\n演示完成。")


if __name__ == "__main__":
    main()
