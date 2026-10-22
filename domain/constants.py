"""领域层常量。护栏写死在这里，不提供配置开关。"""

# ── 推送护栏（硬编码，改要动代码）────────────────────────────────
HARD_LIMITS = {
    "private_per_day": 3,
    "group_per_day": 2,
    "cooldown_hours": 6,
    "quiet_start": 23,
    "quiet_end": 8,
}

# ── 反馈权重 ──────────────────────────────────────────────────
FEEDBACK_WEIGHTS = {
    "positive": 2.0,
    "followup": 1.5,
    "neutral": 0.0,
    "silence": -0.5,
    "negative": -2.0,
}

# ── 画像 ──────────────────────────────────────────────────────
PROFILE_DECAY_DAYS = 30
PROFILE_MAX_KEYWORDS = 50
PROFILE_POSITIVE_DELTA = 1.0
PROFILE_NEGATIVE_DELTA = -1.0
PROFILE_MANUAL_DELTA = 2.0

# ── 推送概率因子 ───────────────────────────────────────────────
FEEDBACK_MULTIPLIER_MIN = 0.2
FEEDBACK_MULTIPLIER_MAX = 2.0
PROBABILITY_CAP = 0.95

# ── 评论禁词（防机器人腔）──────────────────────────────────────
BANNED_COMMENT_PHRASES = (
    "很不错",
    "很有道理",
    "感谢分享",
    "支持一下",
    "期待更多",
    "受益匪浅",
    "首先",
    "其次",
    "总之",
    "一方面",
    "另一方面",
)

# ── 评论约束 ──────────────────────────────────────────────────
COMMENT_MIN_LEN = 8
COMMENT_MAX_LEN = 30
REPLY_MAX_LEN = 100
REPLY_MAX_TOOL_CALLS = 2
REPLY_MAX_PER_ROUND = 2
COMMENT_STYLE_SAMPLE_SIZE = 30
COMMENT_SELF_CHECK_THRESHOLD = 0.7
COMMENT_MAX_REWRITES = 2

# ── 转写约束 ──────────────────────────────────────────────────
TRANSCRIBE_CONCURRENCY = 1
TRANSCRIBE_PER_ROUND = 3
TRANSCRIBE_ITEM_TIMEOUT = 120
TRANSCRIBE_MAX_DURATION = 600

# ── 运行约束 ──────────────────────────────────────────────────
ROUND_TIMEOUT_SECONDS = 600
LLM_CALL_TIMEOUT = 60
BILI_RATE_LIMIT_PER_SEC = 2
MAX_LLM_CALLS_PER_ROUND = 20

# ── 峰谷口径（DeepSeek 官方定价）──────────────────────────────
# 工作日 9-12、14-18 为高峰，其余（含整个周末）为谷时
PEAK_WINDOWS_WEEKDAY = ((9, 12), (14, 18))

# ── 默认关键词 ────────────────────────────────────────────────
DEFAULT_KEYWORDS = (
    "AI",
    "编程",
    "科技",
    "游戏开发",
    "开源",
)

# ── 订阅/退订短语 ─────────────────────────────────────────────
DEFAULT_OPT_IN_PHRASES = (
    "大肥鱼给我发b站的视频",
    "大肥鱼给我发b站视频",
    "订阅b站视频",
    "订阅B站视频",
)

DEFAULT_OPT_OUT_PHRASES = (
    "别发了",
    "取消订阅",
    "不要发了",
)

# ── 中性人设（读不到 persona 时的兜底）────────────────────────
NEUTRAL_PERSONA = "你是一个住在服务器里的 AI 助手，说话简短、诚实，不夸张。"

# ── 指令 ──────────────────────────────────────────────────────
CMD_ROOT = "bili"
CMD_LOGIN = "登录"
CMD_RUN = "跑"
CMD_REPORT = "报告"
CMD_STOP = "停"
CMD_SELFCHECK = "自检"
CMD_STATUS = "状态"
CMD_SUBSCRIBE = "订阅"
CMD_UNSUBSCRIBE = "退订"
