"""日志系统模块"""

from loguru import logger
import os
from pathlib import Path
import sys
from typing import Optional
from config.settings import settings
from config.constants import DATA_DIR, LOGS_DIR


def _ensure_utf8_stdout():
    """尽量强制控制台 stdout 使用 UTF-8，避免 Windows 下 emoji 输出报错。"""
    stdout = sys.stdout
    reconfigure = getattr(stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    return stdout


def _stdout_supports_color(stream) -> bool:
    """重定向到文件时关闭 ANSI 颜色，避免日志文件出现控制字符乱码。"""
    is_tty = getattr(stream, "isatty", None)
    try:
        return bool(is_tty and is_tty())
    except Exception:
        return False


def setup_logger():
    """配置日志系统"""
    return configure_logger(profile="default")


def configure_logger(profile: str = "default", log_level: Optional[str] = None):
    """按运行环境配置日志系统。"""

    # 确保日志目录存在
    log_dir = Path(settings.BASE_DIR) / LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    console_stdout = _ensure_utf8_stdout()
    target_level = log_level or settings.LOG_LEVEL
    console_colorize = _stdout_supports_color(console_stdout)

    # 移除默认的控制台处理器
    logger.remove()

    # 控制台直连终端时保留颜色，重定向到文件时关闭颜色避免乱码。
    logger.add(
        console_stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=target_level,
        colorize=console_colorize,
    )

    rotation = "1 day"
    retention = "30 days"
    compression = None
    file_name = "agent.log"
    error_file_name = "error.log"
    pid_suffix = os.getpid()
    file_name = f"agent.{pid_suffix}.log"
    error_file_name = f"error.{pid_suffix}.log"
    # Mini-PC 环境优先压缩日志体积，避免长期运行占满磁盘。
    if profile == "mini_pc":
        rotation = "20 MB"
        retention = "14 days"
        compression = "zip"

    # 添加文件输出
    logger.add(
        log_dir / file_name,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=target_level,
        rotation=rotation,
        retention=retention,
        compression=compression,
        encoding="utf-8",
        enqueue=True,
    )

    # 添加错误日志文件（只记录ERROR及以上级别）
    logger.add(
        log_dir / error_file_name,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="ERROR",
        rotation="10 MB" if profile == "mini_pc" else "1 day",
        retention="30 days" if profile == "mini_pc" else "90 days",
        compression=compression,
        encoding="utf-8",
        enqueue=True,
    )

    return logger


# 创建全局日志实例
log = setup_logger()


# 日志装饰器
def log_function_call(func):
    """装饰器：记录函数调用"""

    def wrapper(*args, **kwargs):
        log.debug(f"调用函数: {func.__name__}, args: {args}, kwargs: {kwargs}")
        try:
            result = func(*args, **kwargs)
            log.debug(f"函数返回: {func.__name__}, result: {result}")
            return result
        except Exception as e:
            log.exception(f"函数异常: {func.__name__}, error: {e}")
            raise

    return wrapper


def log_async_function_call(func):
    """装饰器：记录异步函数调用"""

    async def wrapper(*args, **kwargs):
        log.debug(f"调用异步函数: {func.__name__}, args: {args}, kwargs: {kwargs}")
        try:
            result = await func(*args, **kwargs)
            log.debug(f"异步函数返回: {func.__name__}, result: {result}")
            return result
        except Exception as e:
            log.exception(f"异步函数异常: {func.__name__}, error: {e}")
            raise

    return wrapper
