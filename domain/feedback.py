"""反馈信号分级。规则优先，判不了才交给 LLM。"""

from __future__ import annotations

from .models import FeedbackKind

# 明确负面：出现即判负
NEGATIVE_PATTERNS = (
    "别发",
    "不要发",
    "取消",
    "无聊",
    "没意思",
    "烦",
    "不想看",
    "难听",
    "垃圾",
    "什么玩意",
)

# 明确正面
POSITIVE_PATTERNS = (
    "好看",
    "有意思",
    "不错",
    "喜欢",
    "还有吗",
    "再来",
    "多来点",
    "收藏了",
    "笑死",
    "绝了",
    "牛逼",
    "厉害",
)

# 追问：说明有兴趣
FOLLOWUP_PATTERNS = (
    "这up",
    "这个up",
    "up主是",
    "谁做的",
    "讲的啥",
    "什么地方",
    "叫什么",
    "哪一期",
    "出处",
    "链接",
    "怎么搜",
)

# 纯敷衍：中性
NEUTRAL_EXACT = (
    "嗯",
    "哦",
    "收到",
    "好的",
    "好",
    "行",
    "6",
    "1",
    "。",
    "...",
    "……",
    "？",
    "?",
)


def classify_by_rules(text: str) -> FeedbackKind | None:
    """能判就判，判不了返回 None 交给 LLM。"""
    body = (text or "").strip()
    if not body:
        return None

    lowered = body.lower()

    for p in NEGATIVE_PATTERNS:
        if p in lowered:
            return FeedbackKind.NEGATIVE

    for p in POSITIVE_PATTERNS:
        if p in lowered:
            return FeedbackKind.POSITIVE

    for p in FOLLOWUP_PATTERNS:
        if p in lowered:
            return FeedbackKind.FOLLOWUP

    if body in NEUTRAL_EXACT:
        return FeedbackKind.NEUTRAL

    # 极短且无上述特征：算中性，不要浪费 LLM
    if len(body) <= 2:
        return FeedbackKind.NEUTRAL

    return None


def is_opt_out(text: str, phrases: tuple[str, ...]) -> bool:
    body = (text or "").strip()
    return any(p in body for p in phrases)


def is_opt_in(text: str, phrases: tuple[str, ...]) -> bool:
    body = (text or "").strip()
    return any(p in body for p in phrases)
