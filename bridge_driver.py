"""BridgeDriver —— 走 TCP + JSON 指挥 Fabric mod 里的 AI 女仆。

和 `bot_client.MCBot` 提供**同名同形**的方法，所以 `main.py` 的 16 个 LLM 工具
一行都不用改，只换驱动即可。

协议（一行一个 JSON）：

    下发：{"id":1,"cmd":"goto","x":10,"z":20}
    响应：{"id":1,"ok":true} / {"id":1,"ok":false,"error":"..."}
    事件：{"event":"mined","block":"minecraft:oak_log","x":..,"y":..,"z":..}

支持：goto / walk / mine / place / give / hold / say / status / stop / jump /
spawn / remove / inv / gamemode

不支持（本驱动会明确返回错误，而不是假装成功）：follow / look_at / 动作队列 /
自动进食 / 环境行为 —— 这些要么等 mod 实现，要么本来就不需要。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any, Callable

from astrbot.api import logger

EVENT_LABEL = {
    "arrived": "到达",
    "mined": "挖掉了",
    "placed": "放下了",
    "hurt": "受伤了",
    "heal": "恢复",
    "food": "饿了",
    "build_complete": "施工完成",
    "build_failed": "施工失败",
    "goal_started": "开始目标",
    "goal_completed": "完成目标",
    "goal_blocked": "目标受阻",
}


class BridgeDriver:
    """连到 mod 的 TCP 桥，指挥那只女仆。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8124,
        *,
        name: str = "sagiri",
        request_timeout: float = 15.0,
        walk_timeout: float = 180.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.name = name
        self.timeout = request_timeout
        self.walk_timeout = walk_timeout

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._seq = 0
        self._connected = False

        self._callbacks: dict[str, Callable] = {}
        self._status: dict[str, Any] = {}
        self.last_error = ""
        self._deciding = False   # 正在等 LLM 决策（防止并发）

    # ==================== 与 MCBot 对齐的接口 ====================
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def position(self) -> tuple[float, float, float] | None:
        maid = self._status.get("maid") or {}
        if not maid:
            return None
        return (maid.get("x"), maid.get("y"), maid.get("z"))

    @property
    def players(self) -> dict[str, str]:
        return {}

    @property
    def action_queue(self):
        return None

    @property
    def behavior_manager(self):
        return None

    @property
    def spawn_position(self):
        return None

    def set_callback(self, name: str, cb: Callable) -> None:
        self._callbacks[name] = cb

    # ==================== 连接 ====================
    async def connect(self) -> str | None:
        """成功返回 None，失败返回原因。"""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=5
            )
        except (OSError, asyncio.TimeoutError) as exc:
            return f"连接女仆桥失败（{self.host}:{self.port}）：{exc}"
        self._connected = True
        self._read_task = asyncio.create_task(self._read_loop())

        # 顺手把状态抓一次；如果桥里没有女仆，刷一个出来
        res, err = await self.request("status")
        if err is None and res is not None:
            self._status = res
            if not res.get("maid"):
                logger.info("桥上还没有女仆，尝试 spawn")
                await self.request("spawn")   # 坐标交给 mod（世界出生点 + 自动找地面）
        logger.info("已连上 AI 女仆桥 %s:%s", self.host, self.port)
        return None

    async def disconnect(self) -> None:
        self._connected = False
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self._writer:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None
        self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    # ==================== 收发 ====================
    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except ValueError:
                    logger.debug("桥发来非 JSON：%s", text[:200])
                    continue
                if "event" in obj:
                    await self._handle_event(obj)
                    continue
                fut = self._pending.pop(obj.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(obj)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("读取桥数据出错")
        finally:
            if self._connected:
                self._connected = False
                logger.warning("与女仆桥的连接断开了")
                cb = self._callbacks.get("on_disconnect")
                if cb:
                    await cb("桥断开", False)

    async def request(
        self, cmd: str, timeout: float | None = None, **kwargs
    ) -> tuple[dict | None, str | None]:
        """发一条指令；返回 (响应, 错误原因)。"""
        if not self._connected or self._writer is None:
            return None, "女仆桥未连接"
        self._seq += 1
        req_id = self._seq
        payload = {"id": req_id, "cmd": cmd}
        payload.update(kwargs)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            self._writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self._writer.drain()
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(req_id, None)
            return None, f"发送失败：{exc}"

        try:
            res = await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return None, f"{cmd} 超时（mod 没回）"
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            raise

        if res.get("ok"):
            return res, None
        return None, str(res.get("error") or "未知错误")

    async def _handle_event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "chat":
            cb = self._callbacks.get("on_chat")
            if cb:
                await cb(ev.get("name"), ev.get("text", ""))
            return
        if kind == "decision_needed":
            # mod 询问"下一步做什么" → 交给 LLM 决策（异步，不阻塞读循环）
            cb = self._callbacks.get("on_decision")
            if cb and not self._deciding:
                self._deciding = True

                async def _run():
                    try:
                        await cb(ev)
                    finally:
                        self._deciding = False

                asyncio.create_task(_run())
            return
        cb = self._callbacks.get("on_event")
        if not cb:
            return
        label = EVENT_LABEL.get(kind, str(kind))
        who = ev.get("who", self.name)
        pos = ""
        if "x" in ev:
            pos = f"（{int(ev['x'])}, {int(ev['y'])}, {int(ev['z'])}）"
        block = f" {ev['block']}" if ev.get("block") else ""
        extra = ""
        if kind == "hurt":
            extra = f" {ev.get('delta')} → {ev.get('health')}"
        elif kind == "food":
            extra = f" 饱食度 {ev.get('food')}"
        elif kind == "build_complete":
            extra = f"：{ev.get('blocks')} 个方块"
        elif kind == "build_failed":
            extra = f"：{ev.get('reason')}"
        await cb(f"【{who}】{label}{block}{pos}{extra}")

    # ==================== 动作（与 MCBot 同形）====================
    async def get_status(self) -> dict[str, Any]:
        res, err = await self.request("status")
        if err is not None or res is None:
            self.last_error = err or ""
            return {}
        self._status = res
        maid = res.get("maid") or {}
        return {
            "username": maid.get("name", self.name),
            "position": (maid.get("x"), maid.get("y"), maid.get("z")) if maid else None,
            "yaw": maid.get("yaw", 0.0),
            "pitch": 0.0,
            "health": float(maid.get("health", 0.0)),
            "food": int(maid.get("food", 0)),
            "players": res.get("players", []),
            "task": maid.get("task", ""),
            "hand": maid.get("hand", ""),
            "inventory": maid.get("inventory", []),
        }

    def player_names(self) -> list[str]:
        return list(self._status.get("players", []))

    async def send_chat(self, message: str) -> bool:
        _, err = await self.request("say", text=message)
        return err is None

    async def move_to(self, x: float, z: float, **_: Any) -> str | None:
        """走到 (x, z)。mod 只回"路径算好了"，所以这里要**等它真的走到**。"""
        _, err = await self.request("goto", x=float(x), z=float(z))
        if err is not None:
            return err
        return await self.wait_arrival(float(x), float(z))

    async def wait_arrival(self, x: float, z: float, tolerance: float = 2.0) -> str | None:
        """轮询状态直到女仆停下且已经到附近。"""
        start = time.monotonic()
        while time.monotonic() - start < self.walk_timeout:
            await asyncio.sleep(0.3)
            st = await self.get_status()
            pos = st.get("position")
            if not pos:
                continue
            dist = math.hypot(pos[0] - x, pos[2] - z)
            if st.get("task") == "idle":
                if dist <= tolerance:
                    return None
                return f"走动中止，还差 {dist:.1f} 格就到 ({x:.0f}, {z:.0f})"
        return f"移动超时（目标 {x:.0f}, {z:.0f}）"

    async def mine(self, x: int, y: int, z: int, **_: Any) -> str | None:
        _, err = await self.request("mine", x=int(x), y=int(y), z=int(z))
        return err

    async def move_and_mine(self, x: int, y: int, z: int, **_: Any) -> str | None:
        """先走到方块附近再挖。"""
        err = await self.move_to(x, z)
        if err is not None:
            return f"走过去失败：{err}"
        return await self.mine(x, y, z)

    async def place_block(self, x: int, y: int, z: int, **_: Any) -> str | None:
        _, err = await self.request("place", x=int(x), y=int(y), z=int(z))
        return err

    async def give(self, item: str, count: int = 64) -> str | None:
        _, err = await self.request("give", item=item, count=int(count))
        return err

    async def hold(self, item: str) -> str | None:
        _, err = await self.request("hold", item=item)
        return err

    async def jump(self) -> str | None:
        _, err = await self.request("jump")
        return err

    async def stop(self) -> str | None:
        _, err = await self.request("stop")
        return err

    async def set_gamemode(self, mode: str) -> str | None:
        _, err = await self.request("gamemode", mode=mode)
        return err

    async def set_goal(self, goal: str) -> str | None:
        """直接给女仆下达目标（如 GATHER_WOOD / BUILD_SHELTER）。"""
        _, err = await self.request("set_goal", goal=str(goal))
        return err

    async def get_goals(self) -> dict[str, Any]:
        res, _ = await self.request("get_goals")
        return res or {}

    async def set_llm_decision(self, enabled: bool = True) -> str | None:
        """开关：女仆是否把"下一步做什么"交给 LLM 决策。"""
        _, err = await self.request("llm_decision", enabled=bool(enabled))
        return err

    # ---- 桥暂时做不到的：明确报错，不假装成功 ----
    async def follow(self, player: str, **_: Any) -> str | None:
        return "bridge 驱动暂不支持跟随玩家；请先用 mc_players 看名字，再 mc_move 到对方坐标"

    async def look_at(self, yaw: float, pitch: float) -> None:
        logger.debug("bridge 驱动忽略 look_at(%s, %s)", yaw, pitch)

    async def eat_food(self) -> str | None:
        return "bridge 驱动暂不支持手动进食（饱食度由 mod 侧原版规则管理）"

    async def collect_drops(self, **_: Any) -> dict[str, Any]:
        return {"collected": 0, "note": "捡东西由原版自动完成"}

    async def attack_entity(self, entity_id: int) -> str | None:
        return "bridge 驱动暂不支持攻击"
