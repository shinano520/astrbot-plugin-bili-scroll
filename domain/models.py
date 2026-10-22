"""领域层数据类。纯数据结构，不含 IO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FeedbackKind(str, Enum):
    POSITIVE = "positive"
    FOLLOWUP = "followup"
    NEUTRAL = "neutral"
    SILENCE = "silence"
    NEGATIVE = "negative"


class Scope(str, Enum):
    PRIVATE = "private"
    GROUP = "group"


class ReportDetail(str, Enum):
    FULL = "full"
    BRIEF = "brief"
    NONE = "none"


@dataclass(frozen=True)
class Video:
    bvid: str
    title: str
    author: str
    pubdate: int
    duration: int = 0
    desc: str = ""
    subtitle: str = ""
    keywords: tuple[str, ...] = ()

    @property
    def has_subtitle(self) -> bool:
        return bool(self.subtitle.strip())


@dataclass(frozen=True)
class Verdict:
    bvid: str
    worth: bool
    reason: str
    one_liner: str = ""
    style: str = ""


@dataclass(frozen=True)
class Subscriber:
    scope: str
    target_id: str
    opted_in: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class PushDecision:
    should_send: bool
    probability: float
    matched_keywords: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ProfileEntry:
    keyword: str
    weight: float


@dataclass
class Profile:
    scope: str
    target_id: str
    entries: dict[str, float] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """累计反馈分，用于反馈系数。"""
        return sum(self.entries.values())

    def match(self, keywords: tuple[str, ...]) -> float:
        """返回 0..1 的匹配度。无关键词返回中性 0.5。"""
        if not keywords or not self.entries:
            return 0.5
        positive = [w for w in self.entries.values() if w > 0]
        if not positive:
            return 0.5
        max_w = max(positive)
        if max_w <= 0:
            return 0.5
        hit = sum(self.entries.get(k, 0.0) for k in keywords)
        # 归一化到 0..1
        raw = hit / (max_w * max(1, len(keywords)))
        return max(0.0, min(1.0, (raw + 1.0) / 2.0))

    def matched_keywords(self, keywords: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(k for k in keywords if self.entries.get(k, 0.0) > 0)


@dataclass
class RoundCost:
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    api_calls: int = 0

    def add(self, other: "RoundCost") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.llm_calls += other.llm_calls
        self.api_calls += other.api_calls

    def render(self) -> str:
        return (
            f"本轮消耗：LLM {self.llm_calls} 次 / "
            f"输入 {self.input_tokens} tokens / 输出 {self.output_tokens} tokens / "
            f"API {self.api_calls} 次"
        )


@dataclass
class RoundResult:
    picked: list[Verdict] = field(default_factory=list)
    skipped: int = 0
    no_subtitle: int = 0
    elapsed: float = 0.0
    cost: RoundCost = field(default_factory=RoundCost)
    stopped: bool = False
    error: str = ""


@dataclass
class DispatchResult:
    sent: int = 0
    skipped_guard: int = 0
    skipped_roll: int = 0


@dataclass
class ReplyResult:
    replied: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class Comment:
    rpid: str
    bvid: str
    content: str
    author: str = ""
    is_direct_reply_to_me: bool = False


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
