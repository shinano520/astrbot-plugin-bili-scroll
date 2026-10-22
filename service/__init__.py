"""应用层：编排。不直接调 SDK，不直接发消息。"""

from .report_service import build_report, render_report
from .scroll_service import ScrollService

__all__ = ["build_report", "render_report", "ScrollService"]
