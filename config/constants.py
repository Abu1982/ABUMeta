"""常量定义模块"""

from typing import Dict, List, Tuple
from datetime import timedelta


# ========== 情感维度定义 ==========
EMOTION_DIMENSIONS: List[str] = [
    "快乐",
    "悲伤",
    "决心",
    "好奇",
    "沮丧",
    "希望",
    "孤独",
    "感恩",
]

# 情感值范围
EMOTION_MIN: float = 0.0
EMOTION_MAX: float = 1.0

# 情感衰减系数（每小时）
EMOTION_DECAY_RATE: float = 0.05


# ========== 焦虑引擎参数 ==========
ANXIETY_THRESHOLD_LOW: float = 0.3  # 低焦虑阈值
ANXIETY_THRESHOLD_MEDIUM: float = 0.6  # 中焦虑阈值
ANXIETY_THRESHOLD_HIGH: float = 0.8  # 高焦虑阈值
ANXIETY_DEFENSE_THRESHOLD: float = 0.8  # 触发心理防御机制阈值
ANXIETY_COOLDOWN_THRESHOLD: float = 0.9  # 触发强制冷静/逃避阈值

# 焦虑影响参数
ANXIETY_TEMPERATURE_FACTOR: float = 0.2  # 焦虑对温度的影响系数
ANXIETY_TOP_P_FACTOR: float = 0.15  # 焦虑对top_p的影响系数
ANXIETY_DEFENSE_TEMPERATURE: float = 0.45  # 心理防御时的温度
ANXIETY_DEFENSE_TOP_P: float = 0.65  # 心理防御时的top_p
ANXIETY_RECOVERY_SLEEP_MULTIPLIER: float = 1.8  # 高焦虑时延长休眠倍数


# ========== 时间系统参数 ==========
# 随机休眠时间（秒）- 使用正态分布
SLEEP_TIME_MEAN: float = 500.0  # 平均500秒（约8分钟）
SLEEP_TIME_STD: float = 150.0  # 标准差150秒
SLEEP_RECOVERY_THRESHOLD: float = 0.8  # 高焦虑时进入恢复性休眠的阈值

# 作息时间
WAKE_UP_TIME: int = 7  # 早7点起床
BED_TIME: int = 23  # 晚11点睡觉

# 夜间响应延迟系数
NIGHT_DELAY_FACTOR: float = 2.0  # 夜间响应延迟2倍


# ========== 金库系统参数 ==========
# 熔断机制阈值
KILL_SWITCH_SINGLE_TRANSACTION_PERCENT: float = 0.10  # 单笔交易最大10%
KILL_SWITCH_DAILY_LOSS_PERCENT: float = 0.10  # 单日最大亏损10%

# 饥饿模式阈值
HUNGER_MODE_THRESHOLD: float = 0.10  # 余额低于10%进入饥饿模式
HUNGER_TOKEN_REDUCTION: float = 0.5  # 饥饿模式下输出减少50%


# ========== 语言掩码参数 ==========
# 禁用词汇列表（必须物理删除）
BLACKLIST_WORDS: List[str] = [
    "作为AI助手",
    "作为一个人工智能",
    "我是一个AI",
    "我是人工智能",
    "AI模型",
    "语言模型",
    "大语言模型",
    "训练数据",
    "我的训练",
    "作为AI",
    "很抱歉我无法",
]

# 语气词库（增加人类感）
FILLER_WORDS: List[str] = [
    "额",
    "嗯",
    "那个",
    "其实吧",
    "讲道理",
    "说真的",
    "说实话",
    "我觉得吧",
    "你知道的",
    "怎么说呢",
]

# 社交主动性话题
SOCIAL_INITIATIVES: List[str] = [
    "你之前提到的那个问题解决了吗？",
    "最近怎么样？",
    "有没有什么新鲜事？",
    "上次我们聊的那个事，后来怎么样了？",
    "今天过得还好吗？",
]


# ========== 记忆系统参数 ==========
# 记忆重要性阈值
MEMORY_IMPORTANCE_THRESHOLD: float = 0.5  # 重要性大于0.5的记忆会被长期保存

# 记忆模糊参数
MEMORY_DECAY_RATE: float = 0.01  # 记忆每月衰减1%
MEMORY_RETRIEVAL_SIMILARITY_THRESHOLD: float = 0.7  # 基础检索相似度阈值
MEMORY_RETRIEVAL_THRESHOLD_DENSITY_FACTOR: float = 0.08  # 记忆密度对阈值的影响系数
MEMORY_RETRIEVAL_THRESHOLD_MIN: float = 0.55
MEMORY_RETRIEVAL_THRESHOLD_MAX: float = 0.88

# 记忆容量
EPISODIC_MEMORY_MAX: int = 1000  # 情景记忆最大条数
SEMANTIC_MEMORY_MAX: int = 5000  # 语义记忆最大条数


# ========== 学习系统参数 ==========
# 网络爬取频率
SCRAPING_INTERVAL_HOURS: int = 6  # 每6小时爬取一次
MAX_ARTICLES_PER_SESSION: int = 10  # 每次最多处理10篇文章

# GitHub趋势监控
GITHUB_TRENDING_URL: str = "https://github.com/trending"
NEWS_SOURCES: List[str] = [
    "https://news.ycombinator.com",
    "https://www.reddit.com/r/programming",
    "https://techcrunch.com",
]
CULTURE_SOURCES: List[str] = [
    "https://en.wikipedia.org/wiki/Minimalism",
    "https://en.wikipedia.org/wiki/Open-source_culture",
    "https://en.wikipedia.org/wiki/Philosophy",
    "https://ctext.org/art-of-war",
]


# ========== 赚钱系统参数 ==========
# 交易参数
MAX_TRANSACTION_AMOUNT: float = 100.0  # 单笔最大交易金额
MIN_PROFIT_MARGIN: float = 0.05  # 最小利润率5%
RISK_TOLERANCE: float = 0.3  # 风险容忍度


# ========== 文件路径 ==========
DATA_DIR: str = "data"
LOGS_DIR: str = "data/logs"
MEMORY_DB_PATH: str = "data/memories.db"
RAW_ARCHIVE_DB_PATH: str = "data/raw_archive.db"
CHROMA_DB_DIR: str = "data/chroma_db"


# ========== 时间常量 ==========
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = 3600
SECONDS_PER_DAY: int = 86400
SECONDS_PER_WEEK: int = 604800


# ========== 目标管理与专注参数 ==========
GOAL_PRIORITY_SWITCH_MARGIN: float = 0.15
GOAL_FOCUS_MAX_PARALLEL: int = 2
GOAL_FOCUS_DEPLETION_PER_TASK: float = 0.12
GOAL_FOCUS_RECOVERY_PER_TICK: float = 0.05
GOAL_SWITCH_WINDOW_SECONDS: float = 120.0
GOAL_SWITCH_COST_BASE_SECONDS: float = 0.2
GOAL_SWITCH_COST_PER_CHANGE_SECONDS: float = 0.15
GOAL_SWITCH_COST_MAX_SECONDS: float = 1.0
GOAL_FOCUS_MIN_LEVEL: float = 0.25
