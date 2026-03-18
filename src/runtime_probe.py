"""项目运行时探针与解释器自校准。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
import os
from pathlib import Path
import sys
from typing import Callable, Iterable


REEXEC_GUARD_ENV = "ABU_RUNTIME_REEXEC"

PROFILE_REQUIREMENTS = {
    "production": ("pydantic", "pydantic_settings", "psutil"),
    "trade_warning": ("pydantic", "pydantic_settings"),
}


@dataclass(frozen=True)
class RuntimeProbeResult:
    """运行时依赖检查结果。"""

    repo_root: str
    current_python: str
    preferred_python: str
    required_modules: tuple[str, ...]
    missing_modules: tuple[str, ...]
    using_preferred_python: bool
    reexec_required: bool
    reexec_guard_active: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_profile_modules(profile: str) -> tuple[str, ...]:
    """根据预设场景返回依赖集合。"""

    normalized = str(profile or "").strip().lower()
    if normalized not in PROFILE_REQUIREMENTS:
        raise ValueError(f"未知运行时画像: {profile}")
    return PROFILE_REQUIREMENTS[normalized]


def collect_runtime_probe(
    repo_root: str | Path,
    *,
    required_modules: Iterable[str],
    current_python: str | Path | None = None,
    module_checker: Callable[[str], bool] | None = None,
) -> RuntimeProbeResult:
    """收集当前解释器的依赖满足情况。"""

    repo_path = Path(repo_root).resolve()
    current = Path(current_python or sys.executable).resolve()
    preferred = _resolve_preferred_python(repo_path)
    checker = module_checker or _default_module_checker
    required = tuple(
        str(module).strip() for module in required_modules if str(module).strip()
    )
    missing = tuple(module for module in required if not checker(module))
    preferred_str = str(preferred) if preferred is not None else ""
    using_preferred = preferred is not None and current == preferred
    reexec_required = bool(missing) and preferred is not None and current != preferred
    return RuntimeProbeResult(
        repo_root=str(repo_path),
        current_python=str(current),
        preferred_python=preferred_str,
        required_modules=required,
        missing_modules=missing,
        using_preferred_python=using_preferred,
        reexec_required=reexec_required,
        reexec_guard_active=os.environ.get(REEXEC_GUARD_ENV) == "1",
    )


def ensure_project_runtime(
    repo_root: str | Path,
    *,
    required_modules: Iterable[str],
    argv: list[str] | None = None,
    module_checker: Callable[[str], bool] | None = None,
) -> RuntimeProbeResult:
    """确保入口脚本运行在满足依赖的项目解释器中。"""

    result = collect_runtime_probe(
        repo_root,
        required_modules=required_modules,
        module_checker=module_checker,
    )
    if not result.missing_modules:
        return result

    if result.reexec_required and not result.reexec_guard_active:
        os.environ[REEXEC_GUARD_ENV] = "1"
        exec_argv = [result.preferred_python, *(argv or sys.argv)]
        os.execv(result.preferred_python, exec_argv)

    raise RuntimeError(format_runtime_probe_error(result))


def format_runtime_probe_error(result: RuntimeProbeResult) -> str:
    """输出可读的运行时错误信息。"""

    missing = ", ".join(result.missing_modules) or "无"
    preferred = result.preferred_python or "<missing>"
    return (
        "ABU 运行时依赖检查失败: "
        f"current_python={result.current_python}; "
        f"preferred_python={preferred}; "
        f"missing_modules={missing}; "
        f"reexec_guard_active={result.reexec_guard_active}"
    )


def _resolve_preferred_python(repo_root: Path) -> Path | None:
    candidates = (
        repo_root / ".venv-gpu" / "Scripts" / "python.exe",
        repo_root / ".venv-gpu" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _default_module_checker(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None
