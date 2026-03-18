"""执行隔离模块。"""

from .sandbox import ShadowRunResult, ShadowSandbox
from .autorepair import (
    AutoRepairManager,
    LoopStrategyManager,
    LoopStrategyState,
    RepairResult,
    RepairTask,
    default_mutable_surfaces,
)
from .execution_log import ExecutionEvent, ExecutionLogger
from .lead_capture import LeadCaptureTarget, SandboxLeadHarvester
from .page_fetcher import PageFetcher, PageFetchResult
from .site_onboarding import SiteOnboardingPlanner
from .tool_discovery import PendingProvisionTask, ToolProvisioner
from .universal_scraping_stack import export_universal_scraping_stack

__all__ = [
    "LeadCaptureTarget",
    "AutoRepairManager",
    "LoopStrategyManager",
    "LoopStrategyState",
    "ExecutionEvent",
    "ExecutionLogger",
    "RepairResult",
    "RepairTask",
    "PageFetcher",
    "PageFetchResult",
    "PendingProvisionTask",
    "SandboxLeadHarvester",
    "ShadowRunResult",
    "ShadowSandbox",
    "SiteOnboardingPlanner",
    "ToolProvisioner",
    "default_mutable_surfaces",
    "export_universal_scraping_stack",
]
