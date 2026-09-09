"""QQ ↔ Minecraft 聊天桥接：订阅会话列表管理与消息推送。

- 订阅列表持久化到插件数据目录（subscribers.json）。
- 玩家用 `/mc 订阅` 把当前会话加入桥接，之后 MC 游戏内聊天会推送到所有
  订阅会话；群聊里以「mc说话」开头的指令也会转发进游戏。

本模块不依赖 AstrBot，只持有一个异步推送回调，便于无头测试。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger("astrbot_plugin_minecraft.bridge")


class ChatBridge:
    """维护订阅会话列表，并向订阅会话广播 MC 聊天。"""

    def __init__(self, data_dir: str | Path, push_cb: Callable[[str, str], None] | None = None):
        """Args:
        data_dir: 插件数据目录（持久化订阅列表）。
        push_cb: 异步推送回调 async (unified_msg_origin, text) -> None，由宿主注入。
        """
        self._file = Path(data_dir) / "mc_subscribers.json"
        self.subscribers: set[str] = set()
        self.push_cb = push_cb or (lambda umo, text: None)
        self._load()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text(encoding="utf-8"))
                self.subscribers = set(data.get("subscribers", []))
        except Exception:  # noqa: BLE001
            logger.warning("读取订阅列表失败，使用空列表")

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps({"subscribers": sorted(self.subscribers)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.warning("保存订阅列表失败")

    # ---------- 订阅管理 ----------
    def add(self, umo: str) -> bool:
        if umo in self.subscribers:
            return False
        self.subscribers.add(umo)
        self._save()
        return True

    def remove(self, umo: str) -> bool:
        if umo not in self.subscribers:
            return False
        self.subscribers.discard(umo)
        self._save()
        return True

    def is_subscribed(self, umo: str) -> bool:
        return umo in self.subscribers

    def list_subscribers(self) -> list[str]:
        return sorted(self.subscribers)

    # ---------- 广播 ----------
    async def broadcast(self, text: str) -> int:
        """把一条 MC 聊天推送到所有订阅会话；返回推送目标数量。"""
        targets = list(self.subscribers)
        for umo in targets:
            try:
                await self.push_cb(umo, text)
            except Exception:  # noqa: BLE001
                logger.warning("推送消息到 %s 失败", umo)
        return len(targets)
