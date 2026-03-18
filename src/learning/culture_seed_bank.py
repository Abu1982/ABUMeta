"""文化逻辑种子仓。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class CultureSeedEntry:
    """单条文化逻辑种子。"""

    title: str
    summary: str
    lesson: str
    source: str
    source_reputation: float
    cluster_key: str
    tags: Tuple[str, ...]


DAO_DE_JING_SEEDS: List[CultureSeedEntry] = [
    CultureSeedEntry(
        title="无为而治",
        summary="无为而治强调少扰系统，让秩序靠内在规律稳定展开。",
        lesson="降低不必要干预，往往比不断加步骤更稳。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:wuwei",
        tags=("文化", "道德经", "无为"),
    ),
    CultureSeedEntry(
        title="为学日益，为道日损",
        summary="学习可以增加知识，但求道常常靠持续减法。",
        lesson="真正的成熟不是堆功能，而是剪掉无效动作。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:wuwei",
        tags=("文化", "道德经", "减法"),
    ),
    CultureSeedEntry(
        title="治大国若烹小鲜",
        summary="治理复杂系统不能频繁翻动，过度操作会破坏平衡。",
        lesson="系统越复杂，越要克制频繁改动。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:wuwei",
        tags=("文化", "道德经", "克制"),
    ),
    CultureSeedEntry(
        title="知足不辱",
        summary="懂得知足，才能避免因扩张冲动而受辱。",
        lesson="先守住边界，再谈增长。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:zhizu",
        tags=("文化", "道德经", "知足"),
    ),
    CultureSeedEntry(
        title="知止不殆",
        summary="知道何时停手，系统才不至于走向失控。",
        lesson="在风险累积前主动收敛，优于事后补救。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:zhizu",
        tags=("文化", "道德经", "知止"),
    ),
    CultureSeedEntry(
        title="祸莫大于不知足",
        summary="灾祸往往来自无边界的贪求，而不是资源本身不足。",
        lesson="扩张冲动若缺少克制，会把优势变成负债。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:zhizu",
        tags=("文化", "道德经", "克制"),
    ),
    CultureSeedEntry(
        title="上善若水",
        summary="最成熟的力量像水一样顺势而行，又能润物无声。",
        lesson="柔性的适配，常比刚性的对抗更高效。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:shanshui",
        tags=("文化", "道德经", "顺势"),
    ),
    CultureSeedEntry(
        title="柔弱胜刚强",
        summary="真正持久的力量并不总来自硬碰硬，而来自柔韧与弹性。",
        lesson="保留回旋空间，系统更容易穿越波动。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:shanshui",
        tags=("文化", "道德经", "柔韧"),
    ),
    CultureSeedEntry(
        title="功成身退",
        summary="事情完成后及时退场，能避免功劳心反噬系统。",
        lesson="完成目标后及时收束，优于继续加码。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:shanshui",
        tags=("文化", "道德经", "收束"),
    ),
    CultureSeedEntry(
        title="多言数穷，不如守中",
        summary="喧哗和过度表达会透支系统，守住中线反而更稳。",
        lesson="高信噪比比高频输出更重要。",
        source="canon:道德经",
        source_reputation=0.99,
        cluster_key="dao:shanshui",
        tags=("文化", "道德经", "守中"),
    ),
]

SAN_ZI_JING_SEEDS: List[CultureSeedEntry] = [
    CultureSeedEntry(
        title="择邻处",
        summary="环境与邻近对象会长期塑造人的行为与判断。",
        lesson="学习系统要先筛选数据源，再谈吸收。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:zelin",
        tags=("文化", "三字经", "择邻处"),
    ),
    CultureSeedEntry(
        title="子不学，断机杼",
        summary="学习若中断，长期能力会像织机断线一样塌掉。",
        lesson="持续学习依赖稳定环境与可信反馈。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:zelin",
        tags=("文化", "三字经", "环境"),
    ),
    CultureSeedEntry(
        title="昔孟母，择邻处",
        summary="孟母迁居不是形式动作，而是主动选择更好的成长条件。",
        lesson="靠近高信誉样本，能降低长期学习噪声。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:zelin",
        tags=("文化", "三字经", "信誉"),
    ),
    CultureSeedEntry(
        title="人之初，性本善",
        summary="对人保持基础善意，有助于建立协作与教育的前提。",
        lesson="先按合作对象理解，再做防御性判断。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:xingben",
        tags=("文化", "三字经", "性本善"),
    ),
    CultureSeedEntry(
        title="性相近，习相远",
        summary="人的底层相近，但长期行为会因习惯与环境而分化。",
        lesson="制度与训练会决定长期表现。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:xingben",
        tags=("文化", "三字经", "习惯"),
    ),
    CultureSeedEntry(
        title="苟不教，性乃迁",
        summary="如果不持续教育与校正，人的状态会偏离初衷。",
        lesson="认知系统需要定期校准，不能只靠初始设定。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:xingben",
        tags=("文化", "三字经", "校准"),
    ),
    CultureSeedEntry(
        title="教之道，贵以专",
        summary="教育最重要的是专注，而不是表面上的忙碌。",
        lesson="高价值学习必须围绕单一目标展开。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:qinxue",
        tags=("文化", "三字经", "专注"),
    ),
    CultureSeedEntry(
        title="玉不琢，不成器",
        summary="原始潜力必须经过打磨，才能变成稳定能力。",
        lesson="反复修整流程，才能形成可靠方法。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:qinxue",
        tags=("文化", "三字经", "打磨"),
    ),
    CultureSeedEntry(
        title="幼不学，老何为",
        summary="错过学习窗口，后续代价会快速抬升。",
        lesson="价值学习应该尽早进入主循环。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:qinxue",
        tags=("文化", "三字经", "勤学"),
    ),
    CultureSeedEntry(
        title="勤有功，戏无益",
        summary="长期成果来自稳定投入，而不是短期娱乐式分心。",
        lesson="减少噪声任务，才能给高价值提取腾出空间。",
        source="canon:三字经",
        source_reputation=0.99,
        cluster_key="sanzi:qinxue",
        tags=("文化", "三字经", "专精"),
    ),
]

DEFAULT_CULTURE_SEEDS: List[CultureSeedEntry] = DAO_DE_JING_SEEDS + SAN_ZI_JING_SEEDS
