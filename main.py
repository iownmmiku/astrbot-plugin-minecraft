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
from astrbot.api.star import Context, Star
from astrbot.core.star.star_tools import StarTools

from .bot_client import MCBot
from .chat_bridge import ChatBridge
from .persona import Persona, build_persona
from .server_manager import LocalServerManager
from .launcher_api_client import LauncherAPIClient
from .goal_system_v2 import GoalManagerV2

PLUGIN_VERSION = "0.3.0"
PLUGIN_NAME = "astrbot_plugin_minecraft"

# 只有机器人真的进服时才激活这些工具：否则任何会话的模型都会带着 16 个「挖方块」工具
LLM_TOOL_NAMES: tuple[str, ...] = (
    "mc_status",
    "mc_move",
    "mc_mine",
    "mc_follow",
    "mc_look",
    "mc_chat",
    "mc_players",
    "mc_submit_move",
    "mc_submit_mine",
    "mc_submit_follow",
    "mc_action_status",
    "mc_cancel_action",
    "mc_move_and_mine",
    "mc_collect_nearby",
    "mc_patrol",
    "mc_return_spawn",
)

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

HELP_TEXT = """【Minecraft 机器人指令】
连接：
  /mc连接 /mc断开        进服 / 退服
  /mc状态                服务器与机器人状态
  /mc玩家                在线玩家
服务器（仅本地模式）：
  /mc起服 /mc停服        启动 / 停止本地服务器
聊天桥：
  /mc订阅 /mc退订        把游戏内聊天推送到本会话
  /mc说话 <内容>         以机器人身份在游戏内发言
动作：
  /mc移动 <x> <z>
  /mc挖掘 <x> <y> <z>
  /mc跟随 <玩家名>
  /mc看向 <yaw> <pitch>
人格与模型：
  /mc人格                查看 / 切换人格来源
  /mc模型                查看 / 切换模型来源
自主游玩：
  /mc目标                查看 / 启动 / 停止目标系统"""


def _cmd_rest(event: AstrMessageEvent) -> str:
    """取指令后的参数部分（去掉开头的 / 与指令名）。"""
    raw = (event.message_str or "").strip().lstrip("/")
    parts = raw.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _llm_response_text(resp: object) -> str | None:
    """从 AstrBot 的 LLMResponse 里安全取出纯文本。

    注意：`str(resp.result_chain)` 拿到的是 MessageChain 的 repr，
    不是回复内容；必须走 `completion_text` / `get_plain_text()`。
    """
    if resp is None:
        return None
    if isinstance(resp, str):
        return resp.strip() or None
    text = getattr(resp, "completion_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    chain = getattr(resp, "result_chain", None)
    get_plain_text = getattr(chain, "get_plain_text", None)
    if callable(get_plain_text):
        plain = get_plain_text() or ""
        if isinstance(plain, str) and plain.strip():
            return plain.strip()
    return None


class MinecraftPlugin(Star):
    """让 AstrBot 机器人以玩家身份进服游玩 Minecraft。

    双向聊天桥（/mc订阅）、动作指令（移动/挖掘/跟随/看向）、以及一整套 mc_*
    LLM 工具，让 AI 能自主决定去哪里、挖什么、说什么。
    输入 /mc帮助 查看全部指令。
    """

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bridge = ChatBridge(self.data_dir, push_cb=self._push_to_subscribers)
        self.server: LocalServerManager | None = None
        self.bot: MCBot | None = None
        self.launcher_api: LauncherAPIClient | None = None
        self._connecting = False
        self._connect_lock = asyncio.Lock()
        self._persona_obj: Persona | None = None
        self._persona_key = ""
        self._persona_prompt_cache = ""
        self.goal_manager: GoalManagerV2 | None = None
        # 后台任务统一登记，terminate 时一起取消（asyncio 只持弱引用，必须自己拿着）
        self._bg_tasks: set[asyncio.Task] = set()
        # LLM 工具是否已激活
        # 工具默认是激活的，等 initialize() 里按进服状态对齐
        self._tools_active = True
        # 游戏内自动回复的节流状态
        self._reply_cooldown: dict[str, float] = {}
        self._reply_lock = asyncio.Lock()

    # ---------- 配置 ----------
    def _cfg(self, key: str, default=None):
        return self.config.get(key, default)

    @property
    def _persona(self) -> Persona:
        """内置 Persona 对象（供空闲自言自语、心情文案使用）。

        有 AstrBot 人格时就用它当人设，否则退到插件自定义文本 / 预设，
        保证自言自语和 LLM 回复是同一个语气。
        """
        desc = (
            self._persona_prompt_cache
            or str(self._cfg("persona_custom_desc", "") or "")
        ).strip()
        persona_id = "custom" if desc else "maid"
        cache_key = f"{persona_id}:{desc}"
        if self._persona_obj is None or self._persona_key != cache_key:
            self._persona_obj = build_persona(
                persona_id,
                username=self._bot_username,
                custom_desc=desc,
            )
            self._persona_key = cache_key
        return self._persona_obj

    @property
    def _bot_username(self) -> str:
        return self._cfg("bot_username", "AstrBot") or "AstrBot"

    def _save_cfg(self, **values) -> None:
        """写入插件配置并落盘（AstrBotConfig 支持 save_config）。"""
        self.config.update(values)
        save = getattr(self.config, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as exc:  # noqa: BLE001
                logger.warning("保存插件配置失败：%s", exc)

    def _persona_source(self) -> str:
        """人格来源：default（AstrBot 当前）/ persona（AstrBot 指定）/ custom（插件自定义）。"""
        source = str(self._cfg("persona_source", "default") or "default")
        return {"astrbot_current": "default", "astrbot_selected": "persona"}.get(
            source, source
        )

    def _bot_target(self) -> tuple[str, int]:
        mode = self._cfg("server_mode", "local")
        if mode == "remote":
            return self._cfg("remote_host", "127.0.0.1"), int(self._cfg("remote_port", 25565))
        elif mode == "launcher_api":
            # launcher_api 模式：从 API 获取服务器地址
            # 默认假设服务器在同一台机器
            api_url = self._cfg("launcher_api_url", "http://127.0.0.1:8765")
            # 提取主机地址（简单解析）
            import urllib.parse
            parsed = urllib.parse.urlparse(api_url)
            host = parsed.hostname or "127.0.0.1"
            port = int(self._cfg("local_port", 25565))  # 使用配置的端口
            return host, port
        return "127.0.0.1", int(self._cfg("local_port", 25565))

    @property
    def _is_local(self) -> bool:
        return self._cfg("server_mode", "local") == "local"
    
    @property
    def _is_launcher_api(self) -> bool:
        return self._cfg("server_mode", "local") == "launcher_api"

    async def initialize(self):
        """插件加载后自动调用；如需自动进服则启动后台任务。"""
        await self._sync_llm_tools(False)  # 先藏起 mc_* 工具，等真的进服再放开
        self._spawn(self._tool_sync_loop())
        if self._cfg("auto_connect", True):
            self._spawn(self._auto_start())

    async def terminate(self):
        """插件卸载/停用时优雅清理。"""
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
        if self.goal_manager:
            self.goal_manager.stop()  # 同步方法，不能 await
            self.goal_manager = None
        if self.bot:
            await self.bot.disconnect()
            self.bot = None
        if self.server:
            await self.server.stop()
            self.server = None
        logger.info("Minecraft 插件已停止")

    # ---------- 后台任务与工具可见性 ----------
    def _spawn(self, coro) -> asyncio.Task:
        """登记后台任务：asyncio 只持弱引用，必须自己拿着，否则可能被 GC。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def _sync_llm_tools(self, active: bool) -> None:
        """按进服状态激活/停用 mc_* 工具，避免污染无关会话的工具列表。"""
        if not bool(self._cfg("manage_llm_tool_activation", True)):
            return
        if active == self._tools_active:
            return
        name = "activate_llm_tool_async" if active else "deactivate_llm_tool_async"
        fn = getattr(self.context, name, None) or getattr(
            self.context, name.replace("_async", ""), None
        )
        if fn is None:
            return
        for tool_name in LLM_TOOL_NAMES:
            try:
                result = fn(tool_name)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.debug("切换工具 %s 状态失败：%s", tool_name, exc)
        self._tools_active = active
        logger.info(
            "Minecraft LLM 工具已%s（%d 个）",
            "激活" if active else "停用",
            len(LLM_TOOL_NAMES),
        )

    async def _tool_sync_loop(self) -> None:
        """周期性对齐「进服状态 ↔ 工具可见性」，顺便刷新人格缓存。"""
        while True:
            try:
                await self._sync_llm_tools(bool(self.bot and self.bot.connected))
                await self._refresh_persona_cache()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("同步 Minecraft 插件状态失败")
            await asyncio.sleep(10)

    async def _refresh_persona_cache(self) -> None:
        """缓存当前生效的人格提示词（_persona 是同步属性，不能 await）。"""
        try:
            self._persona_prompt_cache = await self._system_prompt()
        except Exception as exc:  # noqa: BLE001
            logger.debug("刷新人格缓存失败：%s", exc)

    async def _auto_start(self) -> None:
        try:
            # 根据模式启动服务器
            if self._cfg("enable_server", True) and self._cfg("auto_start_server", True) and self._is_local and await self._ensure_server() is not None:
                err = await self.server.start()
                if err:
                    logger.error("自动启动本地服务器失败：%s", err)
                    return
                if not await self.server.wait_ready():
                    logger.error("本地服务器启动超时")
                    return
                logger.info("本地服务器就绪")
            elif self._is_launcher_api:
                # 启动器 API 模式：通过 API 启动服务器
                api_url = self._cfg("launcher_api_url", "http://127.0.0.1:8765")
                self.launcher_api = LauncherAPIClient(api_url)
                
                # 检查 API 连接
                if not await asyncio.to_thread(self.launcher_api.ping):
                    logger.error("无法连接到启动器 API: %s", api_url)
                    return
                
                logger.info("已连接到启动器 API")
                
                # 获取服务器状态
                status = await asyncio.to_thread(self.launcher_api.get_status)
                if not status.get("running"):
                    # 服务器未运行，尝试启动
                    logger.info("通过启动器 API 启动服务器...")
                    result = await asyncio.to_thread(self.launcher_api.start_server)
                    if not result.get("success"):
                        logger.error("启动器 API 启动服务器失败：%s", result.get("message"))
                        return
                    # 等待服务器就绪
                    await asyncio.sleep(5)
                    logger.info("服务器已通过启动器 API 启动")
                else:
                    logger.info("服务器已在运行中")
            
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
                world_import_dir=self._cfg("local_world_import", ""),
                log_cb=self._server_log,
            )
        return self.server

    def _server_log(self, line: str) -> None:
        logger.info("[mc-server] %s", line)

    async def _connect_bot(self) -> str | None:
        """连接 bot；成功返回 None，失败返回原因。"""
        if self.bot is not None and self.bot.connected:
            return None
        # 用锁保证并发调用（QQ 指令 + 自动重连）不会各连一条
        async with self._connect_lock:
            return await self._connect_bot_locked()

    async def _connect_bot_locked(self) -> str | None:
        self._connecting = True
        try:
            # 驱动二选一：bridge = 指挥服务端 mod 里的 AI 女仆；protocol = 自带协议客户端
            if str(self._cfg("driver", "protocol")).lower() == "bridge":
                from .bridge_driver import BridgeDriver

                self.bot = BridgeDriver(
                    str(self._cfg("bridge_host", "127.0.0.1")),
                    int(self._cfg("bridge_port", 8124)),
                    name=self._bot_username,
                )
                self._wire_bot(self.bot)
                err = await self.bot.connect()
                if err:
                    logger.error("连接女仆桥失败：%s", err)
                    return err
                await self._sync_llm_tools(True)
                logger.info("已通过 bridge 驱动接管 AI 女仆")
                return None

            host, port = self._bot_target()
            # 准备行为配置（阶段 3 + 生动反应层）
            behavior_config = {
                "enable_autonomous_behaviors": bool(self._cfg("enable_autonomous_behaviors", True)),
                "auto_eat_threshold": int(self._cfg("auto_eat_threshold", 10)),
                "auto_flee_enabled": bool(self._cfg("auto_flee_enabled", False)),
                # 生动反应（参考车万女仆）
                "enable_idle_behaviors": bool(self._cfg("enable_idle_behaviors", True)),
                "idle_broadcast_interval": int(self._cfg("idle_broadcast_interval", 180)),
                "idle_llm_generation": bool(self._cfg("idle_llm_generation", True)),
                "idle_hungry_threshold": int(self._cfg("idle_hungry_threshold", 6)),
                "enable_mood": bool(self._cfg("enable_mood", True)),
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
            # 注入人格与发言/LLM 通道（生动反应用）
            if self.bot.behavior_manager:
                self.bot.behavior_manager.persona = self._persona
                self.bot.behavior_manager.set_channel(
                    speak_cb=self._idle_speak,
                    llm_cb=self._idle_llm,
                )
            err = await self.bot.connect()
            if err:
                logger.error("进服失败：%s", err)
                return err
            
            # 连接成功后，启动目标管理器（如果启用）
            if self._cfg("enable_goal_system", False) and self.bot.connected:
                self.goal_manager = GoalManagerV2(
                    self.bot,
                    speak_callback=self._goal_speak,
                    llm_callback=self._goal_llm if self._cfg("goal_use_llm", True) else None
                )
                self.goal_manager.start()
                logger.info("目标管理器 V2 已启动")
            await self._sync_llm_tools(True)  # 进服后才把工具暴露给 LLM
            
            return None
        finally:
            self._connecting = False

    def _wire_bot(self, bot) -> None:
        bot.set_callback("on_chat", self._on_mc_chat)
        bot.set_callback("on_disconnect", self._on_mc_disconnect)
        bot.set_callback("on_event", self._on_bot_event)

    async def _on_bot_event(self, text: str) -> None:
        """女仆主动上报的事件（挖到了 / 受伤 / 饿了 / 到了）→ 转发给订阅者。"""
        await self.bridge.broadcast(f"【MC】{text}")

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
        # 有人跟角色说话 → 心情上升（参考女仆好感）
        if self.bot and self.bot.behavior_manager:
            self.bot.behavior_manager.note_interaction()
        # 游戏内唤醒词检测 → LLM 回复（可选）
        if self._cfg("auto_reply_in_game", True) and sender:
            if self._should_reply_to_message(text):
                self._spawn(self._in_game_llm_reply(sender, text))

    def _should_reply_to_message(self, text: str) -> bool:
        """检查消息是否包含唤醒词或 @ 机器人。"""
        text_lower = text.lower()
        
        # 默认唤醒词：机器人名字（list 或逗号分隔的字符串都接受）
        wake_words = self._cfg("mc_chat_wake_words", [])
        if isinstance(wake_words, str):
            wake_words = [w.strip() for w in re.split(r"[,，\s]+", wake_words) if w.strip()]
        if not wake_words:
            wake_words = [self._bot_username.lower()]
        
        # 检查唤醒词
        for word in wake_words:
            if word.lower() in text_lower:
                return True
        
        # 检查 @ 符号
        if text.lstrip().startswith("@"):
            return True
        
        return False

    async def _on_mc_disconnect(self, reason: str, kicked: bool) -> None:
        logger.warning("机器人断开：%s", reason)
        await self._sync_llm_tools(False)
        await self.bridge.broadcast(f"【MC】机器人已断开：{reason}")

    async def _llm_chat(self, prompt: str, system_prompt: str | None = None) -> str | None:
        """统一 LLM 调用：优先使用 AstrBot 全局配置的模型和人格。"""
        if system_prompt is None:
            system_prompt = await self._system_prompt()
        model = self._llm_model_override()

        try:
            provider = self.context.get_using_provider()
            if provider:
                resp = await provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    model=model,
                    system_prompt=system_prompt,
                )
                text = _llm_response_text(resp)
                if text:
                    return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 主路径失败：%s", exc)

        try:
            provider_id = await self.context.get_current_chat_provider_id("")
            merged = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id, prompt=merged
            )
            text = _llm_response_text(resp)
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 降级路径失败：%s", exc)

        return None

    async def _llm_text(self, prompt: str) -> str | None:
        return await self._llm_chat(prompt)

    async def _idle_llm(self, prompt: str) -> str | None:
        """空闲行为用的 LLM 通道（自动带人设与配置模型）。"""
        return await self._llm_chat(prompt)
    
    async def _goal_llm(self, prompt: str) -> str | None:
        """目标系统用的 LLM 通道（用于目标决策）。"""
        return await self._llm_chat(prompt, system_prompt=None)
    
    async def _goal_speak(self, text: str) -> None:
        """目标系统的发言回调（发送到 MC 和订阅群）。"""
        if self.bot and self.bot.connected:
            await self.bot.send_chat(text[:100])
        await self.bridge.broadcast(f"【MC】{self._bot_username}：{text}")
    
    async def _llm_decide_action(self, context: str) -> str | None:
        """让 LLM 为机器人的下一步行动做出决策。
        
        Args:
            context: 当前情境描述（位置、饥饿度、库存、周围环境等）
        
        Returns:
            LLM 建议的行动（如 "寻找食物"、"建造庇护所"、"探索附近"等）
        """
        prompt = f"""你是 Minecraft 世界中的一个 AI 玩家。

当前状态：
{context}

请根据当前情况，决定接下来应该做什么。只需要简短描述你的计划（20字以内），例如：
- "我需要找食物吃"
- "该建个房子了"
- "去附近探索看看"
- "先收集一些木头"

不要解释原因，只说你要做什么："""
        
        return await self._llm_chat(prompt)

    async def _idle_speak(self, text: str) -> None:
        """把角色的自主发言（小动作/自言自语）发到游戏内聊天 + 推送给订阅者。"""
        if not text:
            return
        # 游戏内说出（第一人称可见）
        if self.bot and self.bot.connected:
            try:
                await self.bot.send_chat(text[:100])
            except Exception:  # noqa: BLE001
                pass
        # 推送到 QQ 群订阅者
        await self.bridge.broadcast(f"【{self._bot_username}】{text}")

    async def _in_game_llm_reply(self, sender: str, text: str) -> None:
        """游戏内 LLM 回复（带冷却与单飞保护，防止刷屏打爆 LLM 配额）。"""
        if not self._reply_allowed(sender):
            logger.debug("忽略 %s 的游戏内消息（冷却中）", sender)
            return
        if self._reply_lock.locked():
            logger.debug("上一句游戏内回复还没说完，忽略 %s 的消息", sender)
            return
        async with self._reply_lock:
            try:
                prompt = (
                    f"玩家 {sender} 在 Minecraft 世界里对你说：{text}\n"
                    f"请以「{self._bot_username}」的身份简短回应（30 字以内），"
                    "口语化，不要加引号和前缀。"
                )
                reply = await self._llm_chat(prompt)
                if reply and self.bot and self.bot.connected:
                    reply = reply.strip().strip('"').strip()
                    await self.bot.send_chat(reply[:100])
                    await self.bridge.broadcast(
                        f"【MC】{self._bot_username}：{reply[:100]}"
                    )
            except Exception:  # noqa: BLE001
                logger.exception("游戏内 LLM 回复失败")

    def _reply_allowed(self, sender: str) -> bool:
        """同一玩家在冷却时间内只回一次。"""
        cooldown = float(self._cfg("in_game_reply_cooldown", 30) or 0)
        if cooldown <= 0:
            return True
        now = time.time()
        key = (sender or "").lower()
        if now - self._reply_cooldown.get(key, 0.0) < cooldown:
            return False
        self._reply_cooldown[key] = now
        if len(self._reply_cooldown) > 200:  # 顺手清理，别让字典无限长大
            self._reply_cooldown = {
                k: v for k, v in self._reply_cooldown.items() if now - v < cooldown
            }
        return True

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
        
        # 添加人格和模型信息
        source = self._persona_source()
        persona_name = {
            "default": "AstrBot 当前人格",
            "persona": f"AstrBot 指定人格（{self._cfg('astrbot_persona_id', '') or '未配置'}）",
            "custom": "插件自定义人格",
        }.get(source, source)
        lines.append(f"人格来源：{persona_name}")

        if str(self._cfg("model_source", "default")) == "custom":
            model_name = f"独立（{self._cfg('llm_model', '') or '未配置'}）"
        else:
            model_name = "跟随 AstrBot"
        lines.append(f"模型来源：{model_name}")
        lines.append(
            "LLM 工具：" + ("已激活" if self._tools_active else "已停用（未进服）")
        )

        return "\n".join(lines)

    def _require_bot(self) -> str | None:
        if self.bot is None or not self.bot.connected:
            return "机器人未进服，请先执行 /mc 连接"
        return None

    async def _load_astrbot_persona_prompt(self) -> str | None:
        """读取 AstrBot 人格提示词（v3 人格的 prompt 字段）。"""
        manager = getattr(self.context, "persona_manager", None)
        if manager is None:
            return None
        try:
            if self._persona_source() == "persona":
                persona_id = str(self._cfg("astrbot_persona_id", "") or "").strip()
                if not persona_id:
                    return None
                persona = manager.get_persona_v3_by_id(persona_id)
                if persona is None:
                    # 旧版人格（数据库），是 async 的
                    legacy = await manager.get_persona(persona_id)
                    prompt = getattr(legacy, "prompt", None) or getattr(
                        legacy, "system_prompt", None
                    )
                    return str(prompt).strip() or None
            else:
                persona = await manager.get_default_persona_v3(None)
            if isinstance(persona, dict):
                prompt = persona.get("prompt") or ""
            else:
                prompt = getattr(persona, "prompt", "") or getattr(
                    persona, "system_prompt", ""
                )
            return str(prompt).strip() or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 AstrBot 人格失败：%s", exc)
            return None

    async def _system_prompt(self) -> str:
        """当前生效的系统提示词。

        1) persona_source=default → AstrBot 当前人格
        2) persona_source=persona → AstrBot 指定人格（astrbot_persona_id）
        3) persona_source=custom  → persona_custom_desc
        任何一环缺失都逐级降级，最后落到插件内置人设。
        """
        source = self._persona_source()
        if source in ("default", "persona"):
            prompt = await self._load_astrbot_persona_prompt()
            if prompt:
                self._persona_prompt_cache = prompt
                return prompt
            if source == "persona":
                logger.warning(
                    "指定人格 %s 不可用，降级为插件内置人设",
                    self._cfg("astrbot_persona_id", ""),
                )
        desc = str(self._cfg("persona_custom_desc", "") or "").strip()
        self._persona_prompt_cache = desc
        return desc or self._persona.system_prompt()

    def _llm_model_override(self) -> str | None:
        """插件独立模型；默认跟随 AstrBot 当前模型。"""
        if str(self._cfg("model_source", "default")) == "custom":
            return str(self._cfg("llm_model", "") or "").strip() or None
        return None

    @filter.command("mc帮助", alias={"mchelp"})
    async def mc_help(self, event: AstrMessageEvent):
        yield event.plain_result(HELP_TEXT)

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

    @filter.command("mc人格", alias={"mcpersona"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_persona(self, event: AstrMessageEvent):
        """查看/切换人格来源（AstrBot 当前 / AstrBot 指定 / 插件自定义）。"""
        raw = _cmd_rest(event).strip()
        parts = raw.split(None, 1)
        head = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if not head:
            source = self._persona_source()
            source_name = {
                "default": "AstrBot 当前人格",
                "persona": "AstrBot 指定人格",
                "custom": "插件自定义人格",
            }.get(source, source)
            info = [f"人格来源：{source_name}"]
            if source == "persona":
                info.append(f"指定人格：{self._cfg('astrbot_persona_id', '') or '（未配置）'}")
            elif source == "custom":
                desc = str(self._cfg("persona_custom_desc", "") or "")
                preview = (desc[:50] + "...") if len(desc) > 50 else desc
                info.append(f"自定义内容：{preview or '（未配置）'}")
            yield event.plain_result(
                "\n".join(info)
                + "\n\n用法：\n"
                "/mc人格 astrbot —— 跟随 AstrBot 当前人格\n"
                "/mc人格 select <人格名> —— 使用 AstrBot 指定人格\n"
                "/mc人格 custom <描述> —— 使用插件自定义人格"
            )
            return

        if head in ("astrbot", "default", "current"):
            self._save_cfg(persona_source="default")
            self._persona_obj = None
            self._persona_prompt_cache = ""
            yield event.plain_result("已切换为 AstrBot 当前人格")
        elif head == "select":
            if not rest:
                yield event.plain_result("请指定人格名：/mc人格 select <人格名>")
                return
            self._save_cfg(persona_source="persona", astrbot_persona_id=rest)
            self._persona_obj = None
            self._persona_prompt_cache = ""
            yield event.plain_result(f"已切换为 AstrBot 指定人格：{rest}")
        elif head == "custom":
            if not rest:
                yield event.plain_result("请提供人格描述：/mc人格 custom <描述>")
                return
            self._save_cfg(persona_source="custom", persona_custom_desc=rest)
            self._persona_obj = None
            self._persona_prompt_cache = ""
            yield event.plain_result(f"已切换为自定义人格（{len(rest)} 字）")
        else:
            yield event.plain_result(
                "未知参数，用法：/mc人格 [astrbot|select <人格名>|custom <描述>]"
            )

    @filter.command("mc模型", alias={"mcmodel"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mc_model(self, event: AstrMessageEvent):
        """查看/切换模型来源（跟随 AstrBot / 插件独立模型）。"""
        raw = _cmd_rest(event).strip()
        parts = raw.split(None, 1)
        head = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if not head:
            source = str(self._cfg("model_source", "default"))
            source_name = "跟随 AstrBot 当前模型" if source == "default" else "插件独立模型"
            info = [f"模型来源：{source_name}"]
            if source == "custom":
                info.append(f"独立模型：{self._cfg('llm_model', '') or '（未配置）'}")
            yield event.plain_result(
                "\n".join(info)
                + "\n\n用法：\n"
                "/mc模型 default —— 跟随 AstrBot 当前模型\n"
                "/mc模型 custom <模型名> —— 使用独立模型"
            )
            return

        if head in ("default", "astrbot"):
            self._save_cfg(model_source="default")
            yield event.plain_result("已切换为跟随 AstrBot 当前模型")
        elif head == "custom":
            if not rest:
                yield event.plain_result("请指定模型名：/mc模型 custom <模型名>")
                return
            self._save_cfg(model_source="custom", llm_model=rest)
            yield event.plain_result(f"已切换为独立模型：{rest}")
        else:
            yield event.plain_result("未知参数，用法：/mc模型 [default|custom <模型名>]")

    @filter.permission_type(filter.PermissionType.ADMIN)

    @filter.command("mc目标", alias={"目标", "mcgoal"})
    async def mc_goal(self, event: AstrMessageEvent):
        """查看/控制目标系统（让机器人知道自己想干什么）。"""
        arg = _cmd_rest(event).strip().lower()
        
        if not arg or arg == "状态":
            # 查看当前目标状态
            if not self.goal_manager:
                yield event.plain_result("目标系统未启用\n用法：/mc目标 启动")
                return
            
            status = self.goal_manager.get_status()
            if not status["running"]:
                yield event.plain_result("目标系统已停止")
                return
            
            current = status.get("current_goal")
            if current:
                yield event.plain_result(
                    f"【当前目标】\n"
                    f"目标：{current['description']}\n"
                    f"进度：{current['progress']*100:.1f}% ({current['current_step']}/{current['total_steps']})\n"
                    f"状态：{current['status']}\n"
                    f"重试：{current['retry_count']}/{current.get('max_retries', 3)}\n"
                    f"错误：{current.get('last_error', '无') if current.get('last_error') else '无'}"
                )
            else:
                yield event.plain_result("暂无当前目标（正在选择中）")
            return
        
        if arg == "启动":
            if self.goal_manager and self.goal_manager.running:
                yield event.plain_result("目标系统已在运行")
                return
            
            if not self.bot or not self.bot.connected:
                yield event.plain_result("请先进服（/mc连接）")
                return
            
            self.goal_manager = GoalManagerV2(
                self.bot,
                speak_callback=self._goal_speak,
                llm_callback=self._goal_llm if self._cfg("goal_use_llm", True) else None
            )
            self.goal_manager.start()
            yield event.plain_result("✓ 目标系统 V2 已启动，机器人开始自主游玩")
            return
        
        if arg == "停止":
            if not self.goal_manager or not self.goal_manager.running:
                yield event.plain_result("目标系统未运行")
                return
            
            self.goal_manager.stop()
            yield event.plain_result("✓ 目标系统已停止")
            return
        
        yield event.plain_result(
            "用法：\n"
            "/mc目标 - 查看当前目标状态\n"
            "/mc目标 启动 - 启动目标系统\n"
            "/mc目标 停止 - 停止目标系统"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)

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

    @filter.permission_type(filter.PermissionType.ADMIN)

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
        await self._sync_llm_tools(False)
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
        return (await self._status_text())

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
            return (err)
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
        
        return (result)

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
            return (err)
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
        
        return (result)

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
            return (err)
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
        
        return (result)

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
            return (err)
            return
        await self.bot.look_at(yaw, pitch)
        direction = "北" if yaw < 45 or yaw >= 315 else "东" if yaw < 135 else "南" if yaw < 225 else "西"
        angle_desc = "仰望天空" if pitch < -45 else "平视前方" if pitch < 45 else "俯视地面"
        return (f"✓ 已转向 {direction}方 ({yaw:.1f}°)，{angle_desc} ({pitch:.1f}°)")

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
            return (err)
            return
        ok = await self.bot.send_chat(message)
        if ok:
            return (f"✓ 已在游戏内说出：「{message}」")
        else:
            return (f"✗ 发送失败（消息为空或超过 256 字）")

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
            return ("机器人未进服")
            return
        names = self.bot.player_names()
        if names:
            return (f"✓ 当前在线 {len(names)} 位玩家：{' 、'.join(names)}")
        else:
            return ("✓ 当前服务器无人（只有我自己）")

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
            return (err)
            return
        if not self.bot.action_queue:
            return ("✗ 动作队列未启用，请使用 mc_move 同步移动")
            return
        action_id = self.bot.action_queue.submit("move", {"x": x, "z": z})
        return (f"✓ 已提交移动动作到 ({x:.1f}, {z:.1f})，action_id: {action_id}")

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
            return (err)
            return
        if not self.bot.action_queue:
            return ("✗ 动作队列未启用，请使用 mc_mine 同步挖掘")
            return
        action_id = self.bot.action_queue.submit("mine", {"x": int(x), "y": int(y), "z": int(z)})
        return (f"✓ 已提交挖掘动作到 ({int(x)}, {int(y)}, {int(z)})，action_id: {action_id}")

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
            return (err)
            return
        if not self.bot.action_queue:
            return ("✗ 动作队列未启用，请使用 mc_follow 同步跟随")
            return
        action_id = self.bot.action_queue.submit("follow", {"player_name": player})
        return (f"✓ 已提交跟随玩家「{player}」的动作，action_id: {action_id}")

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
            return (err)
            return
        if not self.bot.action_queue:
            return ("✗ 动作队列未启用")
            return
        task = self.bot.action_queue.get_status(action_id)
        if task is None:
            return (f"✗ 未找到动作 {action_id}")
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
        return (status_text)

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
            return (err)
            return
        if not self.bot.action_queue:
            return ("✗ 动作队列未启用")
            return
        success = self.bot.action_queue.cancel(action_id)
        if success:
            return (f"✓ 已取消动作 {action_id[:8]}...（动作已从队列移除）")
        else:
            return (f"✗ 无法取消动作 {action_id[:8]}...（可能已在执行中或不存在）")

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
            return (err)
            return
        r = await self.bot.move_and_mine(int(x), int(y), int(z))
        if r is None:
            return (f"✓ 成功移动并挖掘方块 ({int(x)}, {int(y)}, {int(z)})")
        else:
            return (f"✗ 移动挖掘失败：{r}")

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
            return (err)
            return
        r = await self.bot.collect_nearby_blocks(int(x), int(y), int(z), radius)
        if r is None:
            total_blocks = (2 * radius + 1) ** 2
            return (
                f"✓ 成功收集中心点 ({int(x)}, {int(y)}, {int(z)}) 半径 {radius} 格内的方块（约 {total_blocks} 个）"
            )
        else:
            return (f"✗ 收集失败或部分完成：{r}")

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
            return (err)
            return
        r = await self.bot.patrol_area(x1, z1, x2, z2, loops=loops)
        if r is None:
            area_size = abs(x2 - x1) * abs(z2 - z1)
            return (
                f"✓ 完成 {loops} 圈巡逻，区域 ({x1:.1f}, {z1:.1f}) ↔ ({x2:.1f}, {z2:.1f})，面积约 {area_size:.0f} 平方格"
            )
        else:
            return (f"✗ 巡逻失败：{r}")

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
            return (err)
            return
        r = await self.bot.return_to_spawn()
        if r is None:
            spawn = self.bot.spawn_position
            if spawn:
                return (f"✓ 成功返回出生点 ({spawn[0]}, {spawn[1]}, {spawn[2]})")
            else:
                return ("✓ 成功返回出生点")
        else:
            return (f"✗ 返回出生点失败：{r}")


    @filter.llm_tool(name="mc_jump")
    async def llm_mc_jump(self, event: AstrMessageEvent):
        """让 Minecraft 机器人原地跳一下（越过 1 格高的台阶，或从卡住的地方脱困）。

        Args:
            （无参数）
        """
        err = self._require_bot()
        if err:
            return err
        r = await self.bot.jump()
        if r is None:
            return f"✓ 跳了一下，落地坐标 {self.bot.position}"
        return f"✗ 跳跃失败：{r}"

    @filter.llm_tool(name="mc_build")
    async def llm_mc_build(self, event: AstrMessageEvent, blueprint: str, origin_x: int, origin_y: int, origin_z: int):
        """让 AI 女仆按蓝图施工（盖房子、修路、造农场等多方块建筑）。

        女仆会自动：
        1. 检查背包材料是否足够（不够会报告缺什么）
        2. 按"从下到上、从近到远"的顺序依次放置
        3. 每放一块方块推送事件到订阅会话
        4. 施工完成后推送 build_complete 事件

        使用前请确保：
        - 女仆背包里有足够的材料（用 mc_give 提前给）
        - 女仆在创造模式（或给她准备工具）

        执行后请用你的人格向用户汇报施工进度，例如：
        - 开始时：描述即将建造的建筑、材料准备情况
        - 施工中：可以评论建筑风格、施工进度
        - 完成时：表达成就感、邀请用户来参观

        Args:
            blueprint(string): 蓝图字符串，每行一个方块，格式为 x y z 方块ID，行间用换行分隔，示例："0 0 0 stone 换行 1 0 0 stone 换行 0 1 0 stone"
            origin_x(number): 蓝图原点的世界 X 坐标（绝对坐标）
            origin_y(number): 蓝图原点的世界 Y 坐标（绝对坐标）
            origin_z(number): 蓝图原点的世界 Z 坐标（绝对坐标）
        """
        err = self._require_bot()
        if err:
            return err
        
        # 解析蓝图
        plan = []
        for line in blueprint.strip().split("\
"):
            if not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) < 4:
                return f"✗ 蓝图格式错误（行：{line}），应为 'x y z 方块ID'"
            try:
                rx, ry, rz = int(parts[0]), int(parts[1]), int(parts[2])
                block = parts[3]
                plan.append({
                    "x": origin_x + rx,
                    "y": origin_y + ry,
                    "z": origin_z + rz,
                    "block": block if ":" in block else f"minecraft:{block}"
                })
            except (ValueError, IndexError) as e:
                return f"✗ 蓝图解析失败（行：{line}）：{e}"
        
        if not plan:
            return "✗ 蓝图为空"
        
        # 发送 build 指令
        if hasattr(self.bot, "request"):  # bridge 驱动
            res, err = await self.bot.request("build", plan=plan, timeout=300)
            if err:
                return f"✗ 施工失败：{err}"
            blocks = (res or {}).get("blocks", len(plan))
            return f"✓ 开始施工，共 {blocks} 个方块（女仆会自动从下到上依次放置）"
        else:
            return "✗ protocol 驱动暂不支持 build 指令（请切换到 bridge 驱动）"

