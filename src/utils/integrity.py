"""完整性校验模块"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence
import subprocess

from src.utils.logger import log


@dataclass(frozen=True)
class CommandExecutionResult:
    """外部命令执行结果。"""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class IntegrityExpectation:
    """完整性校验预期。"""

    claimed_file_changes: Sequence[str] = ()
    expected_timestamp_updates: Dict[str, datetime] = field(default_factory=dict)
    command_results: Sequence[CommandExecutionResult] = ()


@dataclass(frozen=True)
class IntegrityObservation:
    """完整性校验观测结果。"""

    git_status_lines: List[str]
    git_diff_text: str
    changed_files: List[str]
    timestamp_results: Dict[str, bool]
    command_results: List[CommandExecutionResult]


@dataclass(frozen=True)
class IntegrityReport:
    """完整性校验报告。"""

    status: str
    summary: str
    matched_claims: List[str]
    mismatches: List[str]
    observation: IntegrityObservation

    @property
    def is_success(self) -> bool:
        return self.status == "success"


class IntegrityManager:
    """基于物理证据的执行结果校验器。"""

    FAILURE_PHRASES = {
        "failed": [
            "我得如实说，这一步没有跑通。",
            "我检查过底层结果了，这次尝试失败了。",
            "我不能把它说成完成，因为物理结果对不上。",
        ],
        "partial": [
            "我先如实汇报：有部分结果成立，但还不能算完全完成。",
            "底层证据显示这一步只完成了一部分。",
            "我不能把它包装成成功，目前只能算部分达成。",
        ],
    }

    def __init__(
        self,
        repo_path: Optional[str] = None,
        command_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ):
        self.repo_path = Path(repo_path or Path.cwd())
        self.command_runner = command_runner or subprocess.run
        self.last_report: Optional[IntegrityReport] = None

    def run_command(
        self, command: Sequence[str], timeout: int = 120
    ) -> CommandExecutionResult:
        """执行外部命令并捕获退出码。"""
        try:
            completed = self.command_runner(
                list(command),
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            result = CommandExecutionResult(
                command=" ".join(command),
                exit_code=completed.returncode,
                stdout=(completed.stdout or "").strip(),
                stderr=(completed.stderr or "").strip(),
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandExecutionResult(
                command=" ".join(command),
                exit_code=124,
                stdout=(exc.stdout or "").strip()
                if isinstance(exc.stdout, str)
                else "",
                stderr=(
                    (exc.stderr or "").strip()
                    if isinstance(exc.stderr, str)
                    else f"timeout_after_{timeout}s"
                ),
            )
        log.debug(
            f"🧭 Integrity command | command={result.command} | exit_code={result.exit_code}"
        )
        return result

    def run_git_command(
        self, git_args: Sequence[str], timeout: int = 120
    ) -> CommandExecutionResult:
        """执行 Git 命令并关闭路径转义，确保中文路径可直接比对。"""
        return self.run_command(
            ["git", "-c", "core.quotepath=false", *git_args], timeout=timeout
        )

    def capture_git_status(self) -> List[str]:
        """获取当前 Git 状态。"""
        result = self.run_git_command(["status", "--short"])
        if result.exit_code != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def capture_git_diff(self) -> str:
        """获取当前 Git diff。"""
        result = self.run_git_command(["diff", "--"])
        if result.exit_code != 0:
            return ""
        return result.stdout

    def get_changed_files(self) -> List[str]:
        """获取当前已变更文件列表。"""
        status_lines = self.capture_git_status()
        changed_files: List[str] = []
        for line in status_lines:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            changed_files.append(parts[1].replace("\\", "/"))
        return changed_files

    def verify_timestamp_updates(
        self, expected_timestamp_updates: Dict[str, datetime]
    ) -> Dict[str, bool]:
        """校验文件修改时间是否晚于给定基线。"""
        results: Dict[str, bool] = {}
        for file_path, baseline in expected_timestamp_updates.items():
            path = Path(file_path)
            results[file_path] = (
                path.exists()
                and datetime.fromtimestamp(path.stat().st_mtime) >= baseline
            )
        return results

    def generate_report(
        self, expectation: Optional[IntegrityExpectation] = None
    ) -> IntegrityReport:
        """基于当前物理状态生成完整性报告。"""
        expectation = expectation or IntegrityExpectation()
        git_status_lines = self.capture_git_status()
        git_diff_text = self.capture_git_diff()
        changed_files = self.get_changed_files()
        timestamp_results = self.verify_timestamp_updates(
            expectation.expected_timestamp_updates
        )
        command_results = list(expectation.command_results)

        matched_claims: List[str] = []
        mismatches: List[str] = []

        for file_path in expectation.claimed_file_changes:
            normalized = file_path.replace("\\", "/")
            if normalized in changed_files or normalized in git_diff_text:
                matched_claims.append(f"文件已变更: {normalized}")
            else:
                mismatches.append(f"未检测到声明中的文件变更: {normalized}")

        for file_path, is_updated in timestamp_results.items():
            if is_updated:
                matched_claims.append(f"文件时间戳已更新: {file_path}")
            else:
                mismatches.append(f"文件时间戳未更新或文件不存在: {file_path}")

        for command_result in command_results:
            if command_result.exit_code == 0:
                matched_claims.append(f"命令执行成功: {command_result.command}")
            else:
                mismatches.append(
                    f"命令执行失败: {command_result.command} (exit_code={command_result.exit_code})"
                )

        observation = IntegrityObservation(
            git_status_lines=git_status_lines,
            git_diff_text=git_diff_text,
            changed_files=changed_files,
            timestamp_results=timestamp_results,
            command_results=command_results,
        )

        if mismatches and matched_claims:
            status = "partial"
            summary = f"完整性校验部分通过：{len(matched_claims)} 项匹配，{len(mismatches)} 项不匹配。"
        elif mismatches:
            status = "failed"
            summary = f"完整性校验失败：发现 {len(mismatches)} 项不匹配。"
        else:
            status = "success"
            summary = "完整性校验通过：当前声明与物理证据一致。"

        report = IntegrityReport(
            status=status,
            summary=summary,
            matched_claims=matched_claims,
            mismatches=mismatches,
            observation=observation,
        )
        self.last_report = report
        log.info(
            f"🛡️ Integrity report | status={report.status} | matched={len(matched_claims)} | mismatches={len(mismatches)}"
        )
        return report

    def format_truthful_failure(self, report: IntegrityReport) -> str:
        """生成人设内但不掩盖事实的失败表达。"""
        phrases = self.FAILURE_PHRASES.get(
            report.status, self.FAILURE_PHRASES["failed"]
        )
        opener = phrases[len(report.mismatches) % len(phrases)]
        details = "；".join(report.mismatches[:3]) or report.summary
        return f"{opener} {details}"
