from src.decision import ActionIntent, DecisionBrain
from src.execution import PageFetcher
from src.psyche import PsycheEngine
from src.treasury import TreasuryManager


def test_treasury_stats_smoke():
    stats = TreasuryManager().get_statistics()
    assert "total_balance" in stats
    assert stats["total_balance"] > 0


def test_psyche_smoke():
    psyche = PsycheEngine()
    state = psyche.get_current_state()
    assert "anxiety" in state


def test_decision_brain_smoke():
    brain = DecisionBrain(map_path="evolution_map.json")
    outcome = brain.evaluate_intent(
        ActionIntent(domain="learning", intent_text="整理公开信息")
    )
    assert outcome.action in {
        "allow",
        "lock",
        "reduce_scope",
        "refactor_minimalism",
        "seek_higher_reputation_source",
    }


def test_page_fetcher_analyze_smoke():
    fetcher = PageFetcher()
    result = fetcher.analyze_access(
        url="https://example.com/",
        status_code=200,
        html="<html><head><title>Example</title></head><body><h1>Example</h1></body></html>",
    )
    assert result["page_kind"] in {
        "generic",
        "trade_lead_hub",
        "supplier_directory",
        "product_detail",
    }
