"""工具函数模块"""

from typing import Any, Optional, List, Dict
from datetime import datetime, timedelta
import random
import re
import json
from config.constants import *


def clamp(value: float, min_value: float, max_value: float) -> float:
    """
    限制数值在指定范围内

    Args:
        value: 待限制的值
        min_value: 最小值
        max_value: 最大值

    Returns:
        限制后的值
    """
    return max(min_value, min(value, max_value))


def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """
    计算百分比变化

    Args:
        old_value: 旧值
        new_value: 新值

    Returns:
        百分比变化（负数表示减少）
    """
    if old_value == 0:
        return 0.0
    return ((new_value - old_value) / old_value) * 100


def format_currency(amount: float) -> str:
    """
    格式化货币金额

    Args:
        amount: 金额

    Returns:
        格式化后的字符串（保留2位小数）
    """
    return f"¥{amount:,.2f}"


def parse_datetime(date_str: str) -> Optional[datetime]:
    """
    解析日期时间字符串

    Args:
        date_str: 日期字符串

    Returns:
        解析后的datetime对象，失败返回None
    """
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def generate_random_sleep_time() -> float:
    """
    生成随机休眠时间（使用正态分布）

    使用均值500秒，标准差150秒的正态分布
    确保最小休眠时间为60秒

    Returns:
        休眠时间（秒）
    """
    sleep_time = random.gauss(SLEEP_TIME_MEAN, SLEEP_TIME_STD)
    return max(60.0, sleep_time)  # 确保至少60秒


def is_night_time() -> bool:
    """
    判断当前是否为夜间（23点-7点）

    Returns:
        True表示夜间
    """
    current_hour = datetime.now().hour
    return current_hour < WAKE_UP_TIME or current_hour >= BED_TIME


def apply_night_delay(base_delay: float) -> float:
    """
    应用夜间延迟（夜间响应更慢）

    Args:
        base_delay: 基础延迟时间

    Returns:
        应用夜间系数后的延迟时间
    """
    if is_night_time():
        return base_delay * NIGHT_DELAY_FACTOR
    return base_delay


def sanitize_text(text: str) -> str:
    """
    清理文本，移除特殊字符和多余空白

    Args:
        text: 待清理的文本

    Returns:
        清理后的文本
    """
    # 移除控制字符
    text = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", text)
    # 规范化空白字符
    text = re.sub(r"\s+", " ", text)
    # 去除首尾空白
    text = text.strip()
    return text


def extract_urls(text: str) -> List[str]:
    """
    从文本中提取URL

    Args:
        text: 包含URL的文本

    Returns:
        URL列表
    """
    url_pattern = r"https?://[^\s<>\"']+(?:[^\s<>\"']|\([^\s<>\"']*\))*"
    return re.findall(url_pattern, text)


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    截断文本到指定长度

    Args:
        text: 待截断的文本
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def calculate_moving_average(values: List[float], window_size: int) -> List[float]:
    """
    计算移动平均

    Args:
        values: 数值列表
        window_size: 窗口大小

    Returns:
        移动平均列表
    """
    if not values or window_size <= 0:
        return []

    result = []
    for i in range(len(values)):
        start = max(0, i - window_size + 1)
        window = values[start:i + 1]
        result.append(sum(window) / len(window))

    return result


def deep_merge_dicts(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
    """
    深度合并两个字典

    Args:
        dict1: 第一个字典
        dict2: 第二个字典

    Returns:
        合并后的字典
    """
    result = dict1.copy()

    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


def load_json_file(filepath: str) -> Optional[Dict[str, Any]]:
    """
    加载JSON文件

    Args:
        filepath: 文件路径

    Returns:
        JSON数据，失败返回None
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        from src.utils.logger import log
        log.error(f"加载JSON文件失败: {filepath}, error: {e}")
        return None


def save_json_file(filepath: str, data: Dict[str, Any]) -> bool:
    """
    保存JSON文件

    Args:
        filepath: 文件路径
        data: 待保存的数据

    Returns:
        是否保存成功
    """
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        from src.utils.logger import log
        log.error(f"保存JSON文件失败: {filepath}, error: {e}")
        return False


def exponential_decay(value: float, decay_rate: float, time: float) -> float:
    """
    计算指数衰减

    Args:
        value: 初始值
        decay_rate: 衰减率
        time: 时间

    Returns:
        衰减后的值
    """
    return value * (1 - decay_rate) ** time


def calculate_similarity(str1: str, str2: str) -> float:
    """
    计算两个字符串的相似度（简单实现）

    使用Jaccard相似度

    Args:
        str1: 第一个字符串
        str2: 第二个字符串

    Returns:
        相似度（0-1之间）
    """
    set1 = set(str1)
    set2 = set(str2)
    intersection = set1 & set2
    union = set1 | set2

    if not union:
        return 0.0

    return len(intersection) / len(union)


def generate_unique_id(prefix: str = "") -> str:
    """
    生成唯一ID

    格式: prefix_timestamp_random

    Args:
        prefix: 前缀

    Returns:
        唯一ID
    """
    timestamp = int(datetime.now().timestamp() * 1000)
    random_part = random.randint(1000, 9999)
    return f"{prefix}{timestamp}_{random_part}"


def retry_on_failure(max_attempts: int = 3, delay: float = 1.0):
    """
    装饰器：失败重试

    Args:
        max_attempts: 最大尝试次数
        delay: 重试间隔（秒）

    Returns:
        装饰器函数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            from src.utils.logger import log
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    log.warning(f"函数 {func.__name__} 第 {attempt + 1} 次尝试失败: {e}")
                    if attempt < max_attempts - 1:
                        import time
                        time.sleep(delay)

            log.error(f"函数 {func.__name__} 所有尝试均失败")
            raise last_exception
        return wrapper
    return decorator
