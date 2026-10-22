"""好感度只读适配。读不到就降级，不报错。"""

from __future__ import annotations

from typing import Any

from ..infra.logger import setup

log = setup("bili_scroll.affection")

CANDIDATE_MODULES = (
    "astrbot_plugin_self_learning",
    "data.plugins.astrbot_plugin_self_learning",
)


class AffectionReader:
    """尝试从 astrbot_plugin_self_learning 读好感度。读不到返回 None。"""

    def __init__(self, context: Any = None):
        self.context = context
        self._manager = None
        self._resolved = False

    def _resolve(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        for mod_name in CANDIDATE_MODULES:
            try:
                import importlib

                mod = importlib.import_module(mod_name)
                state = getattr(mod, "state", None)
                if state is None:
                    continue
                mgr = getattr(state, "affection_manager", None)
                if mgr is not None:
                    self._manager = mgr
                    log.info("好感度插件已接入：%s", mod_name)
                    return
            except Exception:
                continue
        log.info("未找到好感度插件，投币/点赞功能将降级")

    @property
    def available(self) -> bool:
        self._resolve()
        return self._manager is not None

    def get(self, user_id: str, group_id: str = "") -> int | None:
        """返回好感度整数；读不到返回 None。"""
        self._resolve()
        if self._manager is None:
            return None
        for attr in ("get_affection", "get_score", "get"):
            fn = getattr(self._manager, attr, None)
            if fn is None:
                continue
            try:
                value = fn(user_id, group_id) if group_id else fn(user_id)
                return int(value)
            except TypeError:
                try:
                    return int(fn(user_id))
                except Exception:
                    continue
            except Exception:
                continue
        return None
