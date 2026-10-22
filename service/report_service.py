"""报告生成。三档：完整 / 薄版 / 不写。"""

from __future__ import annotations

import time
from pathlib import Path

from ..domain.models import ReportDetail, RoundCost, Verdict


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def render_report(
    verdicts: list[Verdict],
    cost: RoundCost | None = None,
    detail: str = "full",
    *,
    no_subtitle: int = 0,
    stopped: bool = False,
    error: str = "",
) -> str | None:
    """返回 Markdown 正文；detail=none 返回 None。"""
    if detail in ("none", ReportDetail.NONE.value):
        return None

    if detail in ("brief", ReportDetail.BRIEF.value):
        lines = [f"## {today_str()} 刷到的", ""]
        for v in verdicts:
            lines.append(f"- [{v.bvid}] {v.one_liner or v.reason}")
        if not verdicts:
            lines.append("- 本轮没挑到")
        lines.append("")
        lines.append(f"（因无字幕跳过 {no_subtitle} 条）")
        return "\n".join(lines)

    lines = [f"# {today_str()} 刷 B 站日报", ""]
    if stopped:
        lines.append("> 本轮被手动停止，以下是已完成部分。")
        lines.append("")
    if error:
        lines.append(f"> 本轮异常：{error}")
        lines.append("")

    if not verdicts:
        lines.append("本轮没挑到值得的。")
    else:
        for v in verdicts:
            lines.append(f"### {v.bvid}")
            lines.append("")
            lines.append(f"**为什么值得**：{v.reason}")
            if v.one_liner:
                lines.append("")
                lines.append(f"**一句话**：{v.one_liner}")
            lines.append("")
            lines.append(f"https://www.bilibili.com/video/{v.bvid}")
            lines.append("")

    lines.append(f"因无字幕跳过 {no_subtitle} 条")
    if cost:
        lines.append("")
        lines.append(cost.render())

    return "\n".join(lines)


def save_report(text: str | None, out_dir: Path) -> Path | None:
    if not text:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today_str()}.md"
    if path.exists():
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n\n---\n\n")
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def read_today(out_dir: Path) -> str:
    path = out_dir / f"{today_str()}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def build_report(
    verdicts: list[Verdict],
    cost: RoundCost | None,
    detail: str,
    out_dir: Path,
    *,
    no_subtitle: int = 0,
    stopped: bool = False,
    error: str = "",
) -> Path | None:
    text = render_report(
        verdicts,
        cost,
        detail,
        no_subtitle=no_subtitle,
        stopped=stopped,
        error=error,
    )
    return save_report(text, out_dir)
