"""AstrBot Minecraft 机器人游玩插件。

功能：
- 让机器人以玩家身份进服（离线账号，mcproto 实现，1.20.1 协议）
- 双向聊天桥：`/mc 订阅` 后 MC 游戏内聊天推送回 QQ 群，QQ 可用 `/mc 说话` 进游戏
- 动作指令：移动 / 挖掘 / 跟随 / 看向 / 状态 / 玩家列表
- LLM 工具：mc_status / mc_move / mc_mine / mc_follow / mc_look / mc_chat / mc_players，
  让 AI 自主驱动机器人「游玩」
- 本地服务器一键自建（Paper 1.20.1）或远程服务器直连
- 断线自动重连、插件卸载优雅退出
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools

from .bot_client import MCBot
from .chat_bridge import ChatBridge
from .server_manager import LocalServerManager

PLUGIN_VERSION = "0.1.0"
PLUGIN_NAME = "astrbot_plugin_minecraft"

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _cmd_rest(event: AstrMessageEvent) -> str:
    """取指令后的参数部分（去掉开头的 / 与指令名）。"""
    raw = (event.message_str or "").strip().lstrip("/")
    parts = raw.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


@register(PLUGIN_NAME, "iownmmiku", "让 AstrBot 机器人进服游玩 Minecraft", PLUGIN_VERSION)
class MinecraftPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bridge = ChatBridge(self.data_dir, push_cb=self._push_to_subscribers)
        self.server: LocalServerManager | None = None
        self.bot: MCBot | None = None
        self._connecting = False

    # ---------- 配置 ----------
    def _cfg(self, key: str, default=None):
        return self.config.get(key, default)

    @property
    def _bot_username(self) -> str:
        return self._cfg("bot_username", "AstrBot") or "AstrBot"

    def _bot_target(self) -> tuple[str, int]:
        if self._cfg("server_mode", "local") == "remote":
            return self._cfg("remote_host", "127.0.0.1"), int(self._cfg("remote_port", 25565))
        return "127.0.0.1", int(self._cfg("local_port", 25565))

    @property
    def _is_local(self) -> bool:
        return self._cfg("server_mode", "local") == "local"

    # ---------- 生命周期 ----------
    async def initialize(self):
        """插件加载后自动调用；如需自动进服则启动后台任务。"""
        if self._cfg("auto_connect", True):
            asyncio.create_task(self._auto_start())

    async def terminate(self):
        """插件卸载/停用时优雅清理。"""
        if self.bot:
            await self.bot.disconnect()
            self.bot = None
        if self.server:
            await self.server.stop()
            self.server = None
        logger.info("Minecraft 插件已停止")

    async def _auto_start(self) -> None:
        try:
            if self._is_local and await self._ensure_server() is not None:
                err = await self.server.start()
                if err:
                    logger.error("自动启动本地服务器失败：%s", err)
                    return
                if not await self.server.wait_ready():
                    logger.error("本地服务器启动超时")
                    return
                logger.info("本地服务器就绪")
            # 服务器刚起好时登录态可能未就绪，重试几次
            err = None
            for attempt in range(1, 4):
                err = await self._connect_bot()
                if err is None:
                    break
                logger.warning("自动进服第 %s 次失败：%s", attempt, err)
                await asyncio.sleep(5)
            if err:
                logger.error("自动进服失败：%s", err)
        except Exception:  # noqa: BLE001
            logger.exception("自动启动流程出错")

    # ---------- 服务器 / 连接 ----------
    async def _ensure_server(self) -> LocalServerManager | None:
        if not self._is_local:
            return None
        if self.server is None:
            self.server = LocalServerManager(
                self.data_dir,
                version=self._cfg("local_version", "1.20.1"),
                port=int(self._cfg("local_port", 25565)),
                motd=self._cfg("local_motd", "AstrBot Minecraft"),
                java_path=self._cfg("java_path", ""),
                log_cb=self._server_log,
            )
        return self.server

    def _server_log(self, line: str) -> None:
        logger.info("[mc-server] %s", line)

    async def _connect_bot(self) -> str | None:
        """连接 bot；成功返回 None，失败返回原因。"""
        if self.bot is not None and self.bot.connected:
            return None
        if self._connecting:
            return "正在连接中，请稍候"
        self._connecting = True
        try:
            host, port = self._bot_target()
            # 准备行为配置（阶段 3）
            behavior_config = {
                "enable_autonomous_behaviors": bool(self._cfg("enable_autonomous_behaviors", True)),
                "auto_eat_threshold": int(self._cfg("auto_eat_threshold", 10)),
                "auto_flee_enabled": bool(self._cfg("auto_flee_enabled", False)),
            }
            self.bot = MCBot(
                host,
                port,
                self._bot_username,
                reconnect_times=int(self._cfg("max_reconnect_times", 5)),
                enable_action_queue=bool(self._cfg("enable_action_queue", True)),
                behavior_config=behavior_config,
                enable_pathfinding=bool(self._cfg("enable_pathfinding", True)),
                pathfinding_max_cost=int(self._cfg("pathfinding_max_cost", 1000)),
            )
            self._wire_bot(self.bot)
            err = await self.bot.connect()
            if err:
                logger.error("进服失败：%s", err)
            return err
        finally:
            self._connecting = False

    def _wire_bot(self, bot: MCBot) -> None:
        bot.set_callback("on_chat", self._on_mc_chat)
        bot.set_callback("on_disconnect", self._on_mc_disconnect)

    # ---------- 回调 ----------
    async def _push_to_subscribers(self, umo: str, text: str) -> None:
        try:
            await self.context.send_message(umo, MessageChain().message(text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("推送消息到 %s 失败：%s", umo, exc)

    async def _on_mc_chat(self, sender: str | None, text: str) -> None:
        if sender and sender.lower() == self._bot_username.lower():
            return  # 忽略机器人自己发出的消息回显
        tag = f"【MC】{sender}：" if sender else "【MC】"
        await self.bridge.broadcast(f"{tag}{text}")
        # 游戏内 @机器人 或点名 → LLM 回复（可选）
        if self._cfg("auto_reply_in_game", True) and sender:
            name = self._bot_username.lower()
            if name in text.lower() or text.lstrip().startswith("@"):
                asyncio.create_task(self._in_game_llm_reply(sender, text))

    async def _on_mc_disconnect(self, reason: str, kicked: bool) -> None:
        logger.warning("机器人断开：%s", reason)
        await self.bridge.broadcast(f"【MC】机器人已断开：{reason}")

    # ---------- LLM ----------
    async def _llm_text(self, prompt: str) -> str | None:
        try:
            provider = self.context.get_using_provider()
            if provider:
                resp = await provider.text_chat(prompt=prompt, contexts=[])
                if resp and resp.result_chain:
                    return str(resp.result_chain)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 主路径失败：%s", exc)
        try:
            pid = self.context.get_current_chat_provider_id("")
            resp = await self.context.llm_generate(chat_provider_id=pid, prompt=prompt)
            if resp and resp.result_chain:
                return str(resp.result_chain)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 降级路径失败：%s", exc)
        return None

    async def _in_game_llm_reply(self, sender: str, text: str) -> None:
        try:
            prompt = (
                f"你是 Minecraft 服务器里的玩家「{self._bot_username}」。玩家 {sender} 对你说：{text}\n"
                "请用简短、口语化的中文回复，不超过 30 个字，不要加引号和前缀。"
            )
            reply = await self._llm_text(prompt)
            if reply and self.bot and self.bot.connected:
                reply = reply.strip().strip('"“”')
                await self.bot.send_chat(reply[:100])
                await self.bridge.broadcast(f"【MC】{self._bot_username}：{reply[:100]}")
        except Exception:  # noqa: BLE001
            logger.exception("游戏内 LLM 回复失败")

    # ---------- 状态文本 ----------
    async def _status_text(self) -> str:
        lines = []
        host, port = self._bot_target()
        mode = "本地" if self._is_local else "远程"
        lines.append(f"服务器({mode})：{host}:{port}")
        if self._is_local and self.server:
            st = self.server.get_status()
            lines.append(f"本地服状态：{st['state']}{'（已就绪）' if st['ready'] else ''}")
        if self.bot and self.bot.connected:
            s = self.bot.get_status()
            pos = s["position"]
            pos_txt = f"{pos[0]}, {pos[1]}, {pos[2]}" if pos else "未同步"
            lines.append(f"机器人：已进服（{s['username']}）")
            lines.append(f"坐标：{pos_txt}  朝向：{s['yaw']}/{s['pitch']}")
            lines.append(f"生命：{s['health']:.0f}/20  饥饿：{s['food']}/20")
            players = s["players"]
            lines.append(f"在线玩家：{'、'.join(players) if players else '无'}")
        else:
            lines.append("机器人：未进服")
        return "\n".join(lines)

    def _require_bot(self) -> str | None:
        if self.bot is None or not self.bot.connected:
            return "机器人未进服，请先执行 /mc 连接"
        return None

    # ================= 指令集 =================
    @filter.command("mc状态", alias={"查服", "mcstatus"})
    async def mc_status(self, event: AstrMessageEvent):
        yield event.plain_result(await self._status_text())

    @filter.command("mc玩家", alias={"在线", "mcplayers"})
    async def mc_players(self, event: AstrMessageEvent):
        if self.bot is None or not self.bot.connected:
            yield event.plain_result("机器人未进服")
            return
        names = self.bot.player_names()
        yield event.plain_result("在线玩家：" + ("、".join(names) if names else "无"))

    @filter.command("mc起服")
    async def mc_start_server(self, event: AstrMessageEvent):
        if not self._is_local:
            yield event.plain_result("当前是远程模式，无需起服")
            return
        mgr = await self._ensure_server()
        if mgr is None:
            yield event.plain_result("服务器管理器初始化失败")
            return
        st = mgr.get_status()
        if st["running"]:
            yield event.plain_result("本地服务器已在运行")
            return
        yield event.plain_result("正在下载/启动本地服务器（首次需要下载约 50MB，请耐心等待）……")
        err = await mgr.start()
        if err:
            yield event.plain_result(f"启动失败：{err}")
            return
        ok = await mgr.wait_ready()
        if ok:
            yield event.plain_result("本地服务器已就绪，可执行 /mc 连接 进服")
        else:
            yield event.plain_result("服务器启动超时，请查看日志")

    @filter.command("mc停服")
    async def mc_stop_server(self, event: AstrMessageEvent):
        if not self._is_local or self.server is None:
            yield event.plain_result("本地服务器未在管理")
            return
        yield event.plain_result("正在停止本地服务器……")
        await self.server.stop()
        yield event.plain_result("本地服务器已停止")

    @filter.command("mc连接", alias={"进服", "mcconnect"})
    async def mc_connect(self, event: AstrMessageEvent):
        if self.bot is not None and self.bot.connected:
            yield event.plain_result("机器人已经进服了")
            return
        yield event.plain_result("正在进服……")
        err = await self._connect_bot()
        yield event.plain_result(f"进服成功！{await self._status_text()}" if err is None else f"进服失败：{err}")

    @filter.command("mc断开", alias={"退服", "mcdisconnect"})
    async def mc_disconnect(self, event: AstrMessageEvent):
        if self.bot is None or not self.bot.connected:
            yield event.plain_result("机器人未进服")
            return
        await self.bot.disconnect()
        yield event.plain_result("机器人已退出服务器")

    @filter.command("mc订阅", alias={"订阅mc"})
    async def mc_subscribe(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        if self.bridge.add(umo):
            yield event.plain_result("已订阅 MC 聊天桥，游戏内聊天将推送到本会话")
        else:
            yield event.plain_result("本会话已经在订阅中")

    @filter.command("mc退订", alias={"取消订阅mc"})
    async def mc_unsubscribe(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        if self.bridge.remove(umo):
            yield event.plain_result("已退订 MC 聊天桥")
        else:
            yield event.plain_result("本会话未在订阅中")

    @filter.command("mc说话")
    async def mc_say(self, event: AstrMessageEvent):
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        text = _cmd_rest(event)
        if not text:
            yield event.plain_result("用法：/mc 说话 <内容>")
            return
        ok = await self.bot.send_chat(text)
        yield event.plain_result("已发送到游戏内" if ok else "发送失败（消息为空或过长）")

    @filter.command("mc移动")
    async def mc_move(self, event: AstrMessageEvent):
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        nums = NUM_RE.findall(_cmd_rest(event))
        if len(nums) < 2:
            yield event.plain_result("用法：/mc 移动 <x> <z>（可带小数）")
            return
        x, z = float(nums[0]), float(nums[1])
        yield event.plain_result(f"正在走向 ({x:.1f}, {z:.1f})……")
        r = await self.bot.move_to(x, z)
        yield event.plain_result(f"到达 ({x:.1f}, {z:.1f})" if r is None else f"移动失败：{r}")

    @filter.command("mc挖掘")
    async def mc_mine(self, event: AstrMessageEvent):
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        nums = NUM_RE.findall(_cmd_rest(event))
        if len(nums) < 3:
            yield event.plain_result("用法：/mc 挖掘 <x> <y> <z>（方块整数坐标）")
            return
        x, y, z = (int(float(n)) for n in nums[:3])
        yield event.plain_result(f"正在挖掘 ({x}, {y}, {z})……")
        r = await self.bot.mine(x, y, z)
        yield event.plain_result(f"挖掘完成 ({x}, {y}, {z})" if r is None else f"挖掘失败：{r}")

    @filter.command("mc跟随")
    async def mc_follow(self, event: AstrMessageEvent):
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        name = _cmd_rest(event).strip()
        if not name:
            yield event.plain_result("用法：/mc 跟随 <玩家名>")
            return
        yield event.plain_result(f"正在跟随玩家「{name}」……")
        r = await self.bot.follow(name)
        yield event.plain_result(f"已跟到「{name}」身边" if r is None else f"跟随失败：{r}")

    @filter.command("mc看向")
    async def mc_look(self, event: AstrMessageEvent):
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        nums = NUM_RE.findall(_cmd_rest(event))
        if len(nums) < 2:
            yield event.plain_result("用法：/mc 看向 <yaw> <pitch>")
            return
        yaw, pitch = float(nums[0]), float(nums[1])
        await self.bot.look_at(yaw, pitch)
        yield event.plain_result(f"已转向 ({yaw:.1f}, {pitch:.1f})")

    # ================= LLM 工具 =================
    @filter.llm_tool(name="mc_status")
    async def llm_mc_status(self, event: AstrMessageEvent):
        """查看 Minecraft 机器人当前的连接状态、坐标、生命值、饥饿值和在线玩家列表。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 健康状态良好时：可以表达精力充沛、心情愉快
        - 生命值或饥饿值偏低时：可以表达疲惫、需要休息或进食的感受
        - 看到在线玩家时：可以表达见到朋友的喜悦或评论玩家
        - 所在位置：可以描述周围环境、评论当前地点
        要像真人玩家在汇报自己状态时的自然反应。

        Args:
            （无参数）
        """
        yield event.plain_result(await self._status_text())

    @filter.llm_tool(name="mc_move")
    async def llm_mc_move(self, event: AstrMessageEvent, x: float, z: float):
        """让 Minecraft 机器人走到指定的 X/Z 坐标（保持当前高度，走直线）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达到达的喜悦、描述沿途见闻、评论目的地
        - 失败时：可以表达困扰、分析原因、提出替代方案
        不要只复述系统数据，要像真人玩家在游戏中的自然反应。

        Args:
            x(number): 目标 X 坐标
            z(number): 目标 Z 坐标
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        
        start_pos = self.bot.position
        r = await self.bot.move_to(x, z)
        
        if r is None:
            # 成功：提供详细信息供 Agent 发挥
            if start_pos:
                import math
                distance = math.hypot(x - start_pos[0], z - start_pos[2])
                result = f"✓ 成功移动到坐标 ({x:.1f}, {z:.1f})，移动了 {distance:.1f} 格"
            else:
                result = f"✓ 成功到达坐标 ({x:.1f}, {z:.1f})"
        else:
            # 失败：提供详细错误信息
            result = f"✗ 移动失败：{r}。当前位置 {self.bot.position}"
        
        yield event.plain_result(result)

    @filter.llm_tool(name="mc_mine")
    async def llm_mc_mine(self, event: AstrMessageEvent, x: float, y: float, z: float):
        """让 Minecraft 机器人挖掘指定整数坐标的方块（距离超过 4 格会失败）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达挖掘的成就感、描述方块类型（如果知道）、评论收获
        - 失败时：可以表达挫折、解释为什么挖不到、建议先移动靠近
        要像真人玩家在挖矿时的自然反应。

        Args:
            x(number): 方块的 X 坐标（整数）
            y(number): 方块的 Y 坐标（整数）
            z(number): 方块的 Z 坐标（整数）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        
        r = await self.bot.mine(int(x), int(y), int(z))
        
        if r is None:
            result = f"✓ 成功挖掘方块 ({int(x)}, {int(y)}, {int(z)})"
            if self.bot.position:
                import math
                dist = math.hypot(int(x) - self.bot.position[0], int(z) - self.bot.position[2])
                result += f"，距离我 {dist:.1f} 格"
        else:
            result = f"✗ 挖掘失败：{r}。目标 ({int(x)}, {int(y)}, {int(z)})"
        
        yield event.plain_result(result)

    @filter.llm_tool(name="mc_follow")
    async def llm_mc_follow(self, event: AstrMessageEvent, player: str):
        """让 Minecraft 机器人跟随指定玩家，保持在其身边 2 格以内。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达跟随的心情、描述和玩家的互动、评论跟随过程
        - 失败时：可以表达找不到人的困扰、询问玩家是否在线、提出等待方案
        要像真人玩家在游戏中跟随队友的自然反应。

        Args:
            player(string): 要跟随的玩家名
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        
        r = await self.bot.follow(player)
        
        if r is None:
            result = f"✓ 成功跟随玩家「{player}」，现在保持在其身边 2 格内"
        else:
            result = f"✗ 跟随失败：{r}"
            # 提供在线玩家列表帮助排查
            if self.bot.players:
                online = ", ".join(self.bot.players.values())
                result += f"。当前在线玩家：{online}"
        
        yield event.plain_result(result)
        r = await self.bot.follow(player)
        yield event.plain_result(f"机器人已跟随玩家「{player}」" if r is None else f"跟随失败：{r}")

    @filter.llm_tool(name="mc_look")
    async def llm_mc_look(self, event: AstrMessageEvent, yaw: float, pitch: float):
        """让 Minecraft 机器人转向指定朝向（yaw 水平角 0-360，pitch 俯仰角 -90 到 90）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 转向时：可以描述看到了什么、评论视野中的景物
        - 仰望天空时：可以表达对天空或星空的感受
        - 俯视地面时：可以表达观察地面的心情
        要像真人玩家在游戏中转动视角的自然反应。

        Args:
            yaw(number): 水平朝向角，0-360 度
            pitch(number): 俯仰角，-90 到 90 度
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        await self.bot.look_at(yaw, pitch)
        direction = "北" if yaw < 45 or yaw >= 315 else "东" if yaw < 135 else "南" if yaw < 225 else "西"
        angle_desc = "仰望天空" if pitch < -45 else "平视前方" if pitch < 45 else "俯视地面"
        yield event.plain_result(f"✓ 已转向 {direction}方 ({yaw:.1f}°)，{angle_desc} ({pitch:.1f}°)")

    @filter.llm_tool(name="mc_chat")
    async def llm_mc_chat(self, event: AstrMessageEvent, message: str):
        """让 Minecraft 机器人在游戏内说一句话（所有游戏内玩家可见）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功发送时：可以表达说话后的心情、期待他人回应
        - 发送失败时：可以表达沮丧、检讨消息内容
        要像真人玩家在游戏聊天时的自然反应。

        Args:
            message(string): 要在游戏内说的话
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        ok = await self.bot.send_chat(message)
        if ok:
            yield event.plain_result(f"✓ 已在游戏内说出：「{message}」")
        else:
            yield event.plain_result(f"✗ 发送失败（消息为空或超过 256 字）")

    @filter.llm_tool(name="mc_players")
    async def llm_mc_players(self, event: AstrMessageEvent):
        """查看 Minecraft 服务器当前在线的玩家名字列表。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 有玩家在线时：可以表达见到朋友的喜悦、评论每个玩家
        - 服务器无人时：可以表达孤独、期待有人加入
        要像真人玩家在查看在线列表时的自然反应。

        Args:
            （无参数）
        """
        if self.bot is None or not self.bot.connected:
            yield event.plain_result("机器人未进服")
            return
        names = self.bot.player_names()
        if names:
            yield event.plain_result(f"✓ 当前在线 {len(names)} 位玩家：{' 、'.join(names)}")
        else:
            yield event.plain_result("✓ 当前服务器无人（只有我自己）")

    # ================= 异步动作队列 LLM 工具（阶段 1）=================
    @filter.llm_tool(name="mc_submit_move")
    async def llm_mc_submit_move(self, event: AstrMessageEvent, x: float, z: float):
        """【异步】提交移动动作到队列，立即返回 action_id，动作在后台执行。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功提交时：可以表达开始行动的决心、预告即将前往的目的地
        - 队列未启用时：可以表达遗憾、建议使用同步方式
        要像真人玩家在规划行动时的自然反应。

        Args:
            x(number): 目标 X 坐标
            z(number): 目标 Z 坐标
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        if not self.bot.action_queue:
            yield event.plain_result("✗ 动作队列未启用，请使用 mc_move 同步移动")
            return
        action_id = self.bot.action_queue.submit("move", {"x": x, "z": z})
        yield event.plain_result(f"✓ 已提交移动动作到 ({x:.1f}, {z:.1f})，action_id: {action_id}")

    @filter.llm_tool(name="mc_submit_mine")
    async def llm_mc_submit_mine(self, event: AstrMessageEvent, x: float, y: float, z: float):
        """【异步】提交挖掘动作到队列，立即返回 action_id，动作在后台执行。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功提交时：可以表达准备挖掘的期待、预告目标方块位置
        - 队列未启用时：可以表达遗憾、建议使用同步方式
        要像真人玩家在准备挖矿时的自然反应。

        Args:
            x(number): 方块的 X 坐标（整数）
            y(number): 方块的 Y 坐标（整数）
            z(number): 方块的 Z 坐标（整数）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        if not self.bot.action_queue:
            yield event.plain_result("✗ 动作队列未启用，请使用 mc_mine 同步挖掘")
            return
        action_id = self.bot.action_queue.submit("mine", {"x": int(x), "y": int(y), "z": int(z)})
        yield event.plain_result(f"✓ 已提交挖掘动作到 ({int(x)}, {int(y)}, {int(z)})，action_id: {action_id}")

    @filter.llm_tool(name="mc_submit_follow")
    async def llm_mc_submit_follow(self, event: AstrMessageEvent, player: str):
        """【异步】提交跟随玩家动作到队列，立即返回 action_id，动作在后台执行。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功提交时：可以表达准备跟随的心情、对目标玩家的评论
        - 队列未启用时：可以表达遗憾、建议使用同步方式
        要像真人玩家在决定跟随队友时的自然反应。

        Args:
            player(string): 要跟随的玩家名
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        if not self.bot.action_queue:
            yield event.plain_result("✗ 动作队列未启用，请使用 mc_follow 同步跟随")
            return
        action_id = self.bot.action_queue.submit("follow", {"player_name": player})
        yield event.plain_result(f"✓ 已提交跟随玩家「{player}」的动作，action_id: {action_id}")

    @filter.llm_tool(name="mc_action_status")
    async def llm_mc_action_status(self, event: AstrMessageEvent, action_id: str):
        """查询异步动作的执行状态（pending/running/completed/failed/cancelled）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 动作进行中时：可以表达耐心等待、描述进度
        - 动作已完成时：可以表达成就感、总结完成的任务
        - 动作失败时：可以表达沮丧、分析失败原因、提出改进建议
        要像真人玩家在查看任务进度时的自然反应。

        Args:
            action_id(string): 动作 ID（由 mc_submit_* 工具返回）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        if not self.bot.action_queue:
            yield event.plain_result("✗ 动作队列未启用")
            return
        task = self.bot.action_queue.get_status(action_id)
        if task is None:
            yield event.plain_result(f"✗ 未找到动作 {action_id}")
            return
        status_text = f"动作 {action_id[:8]}... 状态: {task.status.value}"
        if task.status.value == "running":
            elapsed = time.time() - (task.started_at or task.submitted_at)
            status_text += f"，已运行 {elapsed:.1f} 秒"
        elif task.status.value == "completed":
            duration = (task.completed_at or time.time()) - task.submitted_at
            status_text += f"，已完成（耗时 {duration:.1f} 秒）"
        elif task.status.value == "failed":
            status_text += f"，失败原因: {task.result}"
        yield event.plain_result(status_text)

    @filter.llm_tool(name="mc_cancel_action")
    async def llm_mc_cancel_action(self, event: AstrMessageEvent, action_id: str):
        """取消排队中的动作（仅能取消 pending 状态的动作，running 状态的无法取消）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功取消时：可以表达改变主意的轻松、解释为何取消
        - 无法取消时：可以表达遗憾、说明动作已在执行中无法停止
        要像真人玩家在改变计划时的自然反应。

        Args:
            action_id(string): 动作 ID（由 mc_submit_* 工具返回）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        if not self.bot.action_queue:
            yield event.plain_result("✗ 动作队列未启用")
            return
        success = self.bot.action_queue.cancel(action_id)
        if success:
            yield event.plain_result(f"✓ 已取消动作 {action_id[:8]}...（动作已从队列移除）")
        else:
            yield event.plain_result(f"✗ 无法取消动作 {action_id[:8]}...（可能已在执行中或不存在）")

    # ================= 复合动作 LLM 工具（阶段 2）=================
    @filter.llm_tool(name="mc_move_and_mine")
    async def llm_mc_move_and_mine(self, event: AstrMessageEvent, x: float, y: float, z: float):
        """让 Minecraft 机器人移动到方块附近并挖掘它（自动处理距离过远的情况）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达移动和挖掘的过程体验、描述收获、评论方块类型
        - 失败时：可以表达困扰、分析为什么无法到达或挖掘、提出替代方案
        要像真人玩家在完成挖矿任务时的自然反应。

        Args:
            x(number): 方块的 X 坐标（整数）
            y(number): 方块的 Y 坐标（整数）
            z(number): 方块的 Z 坐标（整数）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        r = await self.bot.move_and_mine(int(x), int(y), int(z))
        if r is None:
            yield event.plain_result(f"✓ 成功移动并挖掘方块 ({int(x)}, {int(y)}, {int(z)})")
        else:
            yield event.plain_result(f"✗ 移动挖掘失败：{r}")

    @filter.llm_tool(name="mc_collect_nearby")
    async def llm_mc_collect_nearby(
        self, event: AstrMessageEvent, x: float, y: float, z: float, radius: int = 5
    ):
        """让 Minecraft 机器人收集指定中心点周围半径内的方块（螺旋扫描挖掘）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达收集的喜悦、统计收获数量、描述挖掘过程的体验
        - 部分完成时：可以表达努力但未完全达成、说明遇到的困难
        - 失败时：可以表达沮丧、分析原因、提出下次如何改进
        要像真人玩家在大规模采集时的自然反应。

        Args:
            x(number): 中心点 X 坐标（整数）
            y(number): 中心点 Y 坐标（整数）
            z(number): 中心点 Z 坐标（整数）
            radius(number): 收集半径（格数，默认 5）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        r = await self.bot.collect_nearby_blocks(int(x), int(y), int(z), radius)
        if r is None:
            total_blocks = (2 * radius + 1) ** 2
            yield event.plain_result(
                f"✓ 成功收集中心点 ({int(x)}, {int(y)}, {int(z)}) 半径 {radius} 格内的方块（约 {total_blocks} 个）"
            )
        else:
            yield event.plain_result(f"✗ 收集失败或部分完成：{r}")

    @filter.llm_tool(name="mc_patrol")
    async def llm_mc_patrol(
        self, event: AstrMessageEvent, x1: float, z1: float, x2: float, z2: float, loops: int = 1
    ):
        """让 Minecraft 机器人在矩形区域巡逻（走四个角点）。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达巡逻的体验、描述沿途见闻、评论巡逻区域
        - 失败时：可以表达困扰、分析中断原因、说明完成了多少圈
        要像真人玩家在执行巡逻任务时的自然反应。

        Args:
            x1(number): 矩形角点 1 的 X 坐标
            z1(number): 矩形角点 1 的 Z 坐标
            x2(number): 矩形角点 2 的 X 坐标（对角）
            z2(number): 矩形角点 2 的 Z 坐标（对角）
            loops(number): 巡逻圈数（默认 1）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        r = await self.bot.patrol_area(x1, z1, x2, z2, loops=loops)
        if r is None:
            area_size = abs(x2 - x1) * abs(z2 - z1)
            yield event.plain_result(
                f"✓ 完成 {loops} 圈巡逻，区域 ({x1:.1f}, {z1:.1f}) ↔ ({x2:.1f}, {z2:.1f})，面积约 {area_size:.0f} 平方格"
            )
        else:
            yield event.plain_result(f"✗ 巡逻失败：{r}")

    @filter.llm_tool(name="mc_return_spawn")
    async def llm_mc_return_spawn(self, event: AstrMessageEvent):
        """让 Minecraft 机器人返回出生点。
        
        执行后请用你的人格和语气向用户汇报结果，例如：
        - 成功时：可以表达回家的温暖、描述返回的旅程、评论出生点
        - 失败时：可以表达迷路的困扰、说明不知道出生点在哪、提出寻找方案
        要像真人玩家在回家时的自然反应。

        Args:
            （无参数）
        """
        err = self._require_bot()
        if err:
            yield event.plain_result(err)
            return
        r = await self.bot.return_to_spawn()
        if r is None:
            spawn = self.bot.spawn_position
            if spawn:
                yield event.plain_result(f"✓ 成功返回出生点 ({spawn[0]}, {spawn[1]}, {spawn[2]})")
            else:
                yield event.plain_result("✓ 成功返回出生点")
        else:
            yield event.plain_result(f"✗ 返回出生点失败：{r}")

