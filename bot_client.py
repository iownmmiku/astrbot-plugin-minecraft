"""mcproto 实现的 Minecraft 1.20.1 (protocol 763) 游戏客户端 bot。

设计说明：
- mcproto 0.6.0 仅内置 handshaking/login/status 状态的包，play 状态包在本模块
  内按 1.20.1 协议自行定义（包 ID 与字段均核对自 minecraft-data 协议表）。
- 机器人以离线（非正版）账号登录，仅适配未开启正版验证（online-mode=false）的
  服务器；若服务器要求加密登录（正版验证），登录流程会直接失败并给出明确提示。
- 本模块不依赖 AstrBot，只依赖 mcproto 与标准库，便于无头测试。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from typing import Any, Callable, final

from attrs import define
from mcproto.buffer import Buffer
from mcproto.connection import TCPAsyncConnection
from mcproto.packets.interactions import async_write_packet
from mcproto.packets.login.login import (
    LoginAcknowledged,
    LoginDisconnect,
    LoginSetCompression,
    LoginStart,
    LoginSuccess,
)
from mcproto.packets.packet import GameState, ServerBoundPacket
from mcproto.protocol.base_io import StructFormat
from mcproto.types.uuid import UUID

logger = logging.getLogger("astrbot_plugin_minecraft.bot")

PROTOCOL_VERSION = 763  # Minecraft 1.20.1

# ---- clientbound play 包 ID（protocol 763）----
CB_NAMED_ENTITY_SPAWN = 0x03
CB_SET_CONTAINER_CONTENT = 0x11
CB_SET_SLOT = 0x15
CB_KEEP_ALIVE = 0x23
CB_LOGIN = 0x28
CB_REL_ENTITY_MOVE = 0x2B
CB_ENTITY_MOVE_LOOK = 0x2C
CB_ENTITY_LOOK = 0x2D
CB_PING = 0x32
CB_PLAYER_CHAT = 0x35
CB_PLAYER_REMOVE = 0x39
CB_PLAYER_INFO = 0x3A
CB_PROFILELESS_CHAT = 0x1B
CB_POSITION = 0x3C
CB_ENTITY_DESTROY = 0x3E
CB_RESPAWN = 0x41
CB_UPDATE_HEALTH = 0x57
CB_SYSTEM_CHAT = 0x64
CB_ENTITY_TELEPORT = 0x68
CB_KICK_DISCONNECT = 0x1A
CB_ABILITIES = 0x34
CB_SPAWN_POSITION = 0x50

# ---- serverbound play 包 ID ----
SB_TELEPORT_CONFIRM = 0x00
SB_CHAT_MESSAGE = 0x05
SB_CLIENT_COMMAND = 0x07
SB_SETTINGS = 0x08
SB_KEEP_ALIVE = 0x12
SB_POSITION = 0x14
SB_POSITION_LOOK = 0x15
SB_LOOK = 0x16
SB_FLYING = 0x17
SB_BLOCK_DIG = 0x1D
SB_BLOCK_PLACE = 0x31
SB_PONG = 0x20
SB_HELD_ITEM_SLOT = 0x28
SB_ARM_ANIMATION = 0x2F
SB_USE_ITEM = 0x32

# 挖掘状态（PlayerDigging）
DIG_START = 0
DIG_CANCEL = 1
DIG_FINISH = 2

# 方块的 6 个面：0=下(-Y) 1=上(+Y) 2=北(-Z) 3=南(+Z) 4=西(-X) 5=东(+X)
FACE_DOWN, FACE_UP, FACE_NORTH, FACE_SOUTH, FACE_WEST, FACE_EAST = 0, 1, 2, 3, 4, 5

WALK_SPEED = 4.317  # 玩家步行速度 m/s（疾跑 5.6，这里用普通步行）
REACH_DISTANCE = 4.5  # 玩家交互/挖掘最大距离
MOVE_STEP = 0.22      # 每个移动包的最大步进（格），需 < 0.25（服务器 moved wrongly 阈值）
MOVE_TICK = 0.05      # 移动发送间隔（秒），20Hz


def pack_position(x: int, y: int, z: int) -> int:
    """把方块坐标打包成 64 位 position（X 26 位 / Z 26 位 / Y 12 位）。"""
    val = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
    # 转换为有符号 i64（struct.pack('q') 需要）
    if val >= (1 << 63):
        val -= (1 << 64)
    return val


def unpack_position(value: int) -> tuple[int, int, int]:
    """把 64 位 position 解包成方块坐标。"""
    x = value >> 38
    z = (value >> 12) & 0x3FFFFFF
    y = value & 0xFFF
    # 符号位还原
    if x >= 1 << 25:
        x -= 1 << 26
    if z >= 1 << 25:
        z -= 1 << 26
    if y >= 1 << 11:
        y -= 1 << 12
    return x, y, z


# 常见系统消息的 translate 键 → 中文模板（%s 为 with 参数）
TRANSLATE_MAP = {
    "multiplayer.player.joined": "%s 加入了游戏",
    "multiplayer.player.left": "%s 离开了游戏",
    "chat.type.text": "<%s> %s",
    "chat.type.announcement": "[公告] %s",
    "chat.type.emote": "* %s %s",
    "death.attack.generic": "%s 死了",
    "multiplayer.disconnect.genericReason": "被断开：%s",
    "disconnect.genericReason": "%s",
    "disconnect.endOfStream": "连接中断",
    "disconnect.timeout": "连接超时",
    "multiplayer.disconnect.kicked": "被服务器踢出：%s",
}


def extract_chat_text(content: str) -> str:
    """把聊天 JSON 组件（1.20.1 system_chat 内容）里的纯文本提取出来。

    兼容 1.7+ 的 NBT 字符串组件 / 纯字符串 / 带 color 等额外字段的对象组件，
    递归收集所有 text 与 translate（含 with 参数）字段；常见 translate 键做中文翻译。
    """
    if not content:
        return ""
    text = content.strip()
    if not text.startswith("{"):
        return text
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return text

    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            if isinstance(node.get("text"), str):
                parts.append(node["text"])
            if isinstance(node.get("translate"), str):
                key = node["translate"]
                with_args = node.get("with", [])
                template = TRANSLATE_MAP.get(key)
                if template is not None and with_args:
                    args = [_flatten(a) for a in with_args]
                    try:
                        parts.append(template % tuple(args))
                    except (TypeError, ValueError):
                        parts.append(key + "".join(args))
                elif template is not None:
                    parts.append(template)
                else:
                    parts.append(key)
                    for arg in with_args:
                        walk(arg)
            for child in node.get("extra", []):
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(obj)
    return "".join(parts).strip()


def _flatten(node: Any) -> str:
    """把一个 JSON 组件节点压成纯文本（用于 translate 的 with 参数）。"""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            return node["text"]
        return "".join(_flatten(c) for c in node.get("extra", []))
    if isinstance(node, list):
        return "".join(_flatten(c) for c in node)
    return str(node)


class PlayServerBoundPacket(ServerBoundPacket):
    """play 状态下 client->server 的包基类。"""

    GAME_STATE = GameState.PLAY
    __slots__ = ()

    @classmethod
    def _deserialize(cls, buf: Buffer, /):
        raise NotImplementedError("本插件只发送该包，不需要反序列化")

    def validate(self) -> None:
        pass


@final
@define
class SBTTeleportConfirm(PlayServerBoundPacket):
    PACKET_ID = SB_TELEPORT_CONFIRM

    teleport_id: int

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_varint(self.teleport_id)


@final
@define
class SBTChatMessage(PlayServerBoundPacket):
    """ServerBound Chat Message (1.20.1)：

    message / timestamp(i64 毫秒) / salt(i64) / signature(可选 256B) /
    offset(varint，最近一条已读消息序号，没有已读消息时用 0；-1 会被服务器拒绝) /
    acknowledged(3 字节位图)
    """

    PACKET_ID = SB_CHAT_MESSAGE

    message: str
    timestamp: int
    salt: int
    signature: bytes | None = None
    offset: int = 0
    acknowledged: bytes = b"\x00\x00\x00"

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_utf(self.message)
        buf.write_value(StructFormat.LONGLONG, self.timestamp)
        buf.write_value(StructFormat.LONGLONG, self.salt)
        buf.write_optional(self.signature, buf.write_bytearray)
        buf.write_varint(self.offset)
        buf.write(self.acknowledged)


@final
@define
class SBTClientCommand(PlayServerBoundPacket):
    PACKET_ID = SB_CLIENT_COMMAND

    action: int  # 0=执行重生

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_varint(self.action)


@final
@define
class SBTSettings(PlayServerBoundPacket):
    """ServerBound Client Information (1.20.1)。"""

    PACKET_ID = SB_SETTINGS

    locale: str = "zh_cn"
    view_distance: int = 8
    chat_flags: int = 0  # 0=显示全部聊天
    chat_colors: bool = True
    skin_parts: int = 0x7F
    main_hand: int = 0  # 0=右手
    enable_text_filtering: bool = False
    enable_server_listing: bool = True

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_utf(self.locale)
        buf.write_value(StructFormat.BYTE, self.view_distance)
        buf.write_varint(self.chat_flags)
        buf.write_value(StructFormat.BOOL, self.chat_colors)
        buf.write_value(StructFormat.UBYTE, self.skin_parts)
        buf.write_varint(self.main_hand)
        buf.write_value(StructFormat.BOOL, self.enable_text_filtering)
        buf.write_value(StructFormat.BOOL, self.enable_server_listing)


@final
@define
class SBTKeepAlive(PlayServerBoundPacket):
    PACKET_ID = SB_KEEP_ALIVE

    keep_alive_id: int

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_value(StructFormat.LONGLONG, self.keep_alive_id)


@final
@define
class SBTPosition(PlayServerBoundPacket):
    PACKET_ID = SB_POSITION

    x: float
    y: float
    z: float
    on_ground: bool = True

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_value(StructFormat.DOUBLE, self.x)
        buf.write_value(StructFormat.DOUBLE, self.y)
        buf.write_value(StructFormat.DOUBLE, self.z)
        buf.write_value(StructFormat.BOOL, self.on_ground)


@final
@define
class SBTPositionLook(PlayServerBoundPacket):
    PACKET_ID = SB_POSITION_LOOK

    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    on_ground: bool = True

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_value(StructFormat.DOUBLE, self.x)
        buf.write_value(StructFormat.DOUBLE, self.y)
        buf.write_value(StructFormat.DOUBLE, self.z)
        buf.write_value(StructFormat.FLOAT, self.yaw)
        buf.write_value(StructFormat.FLOAT, self.pitch)
        buf.write_value(StructFormat.BOOL, self.on_ground)


@final
@define
class SBTPong(PlayServerBoundPacket):
    PACKET_ID = SB_PONG

    id: int

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_value(StructFormat.INT, self.id)


@final
@define
class SBTUseItem(PlayServerBoundPacket):
    """Use Item (0x32)：使用手持物品（吃食物、喝药水等）。"""
    PACKET_ID = SB_USE_ITEM

    hand: int  # 0=主手，1=副手
    sequence: int = 0

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_varint(self.hand)
        buf.write_varint(self.sequence)


@final
@define
class SBTHeldItemSlot(PlayServerBoundPacket):
    """Held Item Change (0x28)：切换手持物品栏位（0-8）。"""
    PACKET_ID = SB_HELD_ITEM_SLOT

    slot: int  # 0-8

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_value(StructFormat.SHORT, self.slot)


@final
@define
class SBTBlockDig(PlayServerBoundPacket):
    """ServerBound Player Digging (1.20.1)：status / location(打包 position) / face / sequence"""

    PACKET_ID = SB_BLOCK_DIG

    status: int
    x: int
    y: int
    z: int
    face: int
    sequence: int = 0

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_varint(self.status)
        buf.write_value(StructFormat.LONGLONG, pack_position(self.x, self.y, self.z))
        buf.write_value(StructFormat.BYTE, self.face)
        buf.write_varint(self.sequence)


@final
@define
class SBTArmAnimation(PlayServerBoundPacket):
    PACKET_ID = SB_ARM_ANIMATION

    hand: int = 0

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_varint(self.hand)


@final
@define
class SBTBlockPlace(PlayServerBoundPacket):
    """ServerBound Use Item On / Block Place (1.20.1)：在方块表面放置物品。
    
    hand / location(打包 position) / face / cursor_x/y/z / inside_block / sequence
    """

    PACKET_ID = SB_BLOCK_PLACE

    hand: int  # 0=主手，1=副手
    x: int
    y: int
    z: int
    face: int  # 放置在哪个面：0=下 1=上 2=北 3=南 4=西 5=东
    cursor_x: float = 0.5
    cursor_y: float = 0.5
    cursor_z: float = 0.5
    inside_block: bool = False
    sequence: int = 0

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_varint(self.hand)
        buf.write_value(StructFormat.LONGLONG, pack_position(self.x, self.y, self.z))
        buf.write_varint(self.face)
        buf.write_value(StructFormat.FLOAT, self.cursor_x)
        buf.write_value(StructFormat.FLOAT, self.cursor_y)
        buf.write_value(StructFormat.FLOAT, self.cursor_z)
        buf.write_value(StructFormat.BOOL, self.inside_block)
        buf.write_varint(self.sequence)


class LoginStart120:
    """1.20.1 的 LoginStart：username + Optional<UUID>。

    mcproto 自带的 LoginStart 按新版本（UUID 必填）序列化，与 1.20.1 不符，
    这里直接写裸包：声明无 UUID。
    """

    PACKET_ID = 0x00

    def __init__(self, username: str):
        self.username = username

    def serialize(self) -> Buffer:
        buf = Buffer()
        buf.write_varint(self.PACKET_ID)
        buf.write_utf(self.username)
        buf.write_value(StructFormat.BOOL, False)
        return buf


class MCBot:
    """一个可进服游玩的 Minecraft 客户端 bot。

    回调（通过 set_callback 注册，全部为 async callable）：
      - on_ready: 进服完成
      - on_chat(sender, text): 收到玩家/系统聊天
      - on_disconnect(reason, kicked): 被踢/断开
      - on_health(health, food): 生命/饥饿变化
      - on_player_list(names): 在线玩家列表变化
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        *,
        reconnect_times: int = 0,
        enable_action_queue: bool = True,
        behavior_config: dict | None = None,
        enable_pathfinding: bool = True,
        pathfinding_max_cost: int = 1000,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.reconnect_times = reconnect_times

        self.conn: TCPAsyncConnection | None = None
        self.compression_threshold = -1
        self._compression_enabled = False
        self.connected = False

        self.entity_id: int | None = None
        self.position: tuple[float, float, float] | None = None
        self.yaw = 0.0
        self.pitch = 0.0
        self.health = 20.0
        self.food = 20
        self.food_saturation = 5.0
        
        # 库存系统：slot_id -> {item_id, count, nbt}
        # slot 0-8: 快捷栏，9-35: 主背包，36-39: 盔甲栏，40: 副手
        self.inventory: dict[int, dict[str, Any]] = {}
        self.held_slot = 0  # 当前手持物品栏位 (0-8)

        self.players: dict[str, str] = {}       # uuid -> 玩家名
        self.entities: dict[int, dict[str, Any]] = {}  # entityId -> {uuid, x, y, z}

        self._callbacks: dict[str, Callable] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = asyncio.Event()
        self._stopping = False
        self._dig_sequence = 0
        self._last_chat_index = -1
        
        # 出生点坐标（阶段 2）
        self.spawn_position: tuple[float, float, float] | None = None

        # 异步动作队列（阶段 1）
        from .action_queue import ActionQueue
        self.action_queue = ActionQueue(self) if enable_action_queue else None
        
        # 自主行为管理器（阶段 3）
        from .autonomous_behaviors import AutonomousBehaviorManager
        self.behavior_manager = AutonomousBehaviorManager(self, behavior_config or {}) if behavior_config else None
        
        # 智能寻路系统（阶段 4）
        from .pathfinding import PathfindingEngine, WorldMap
        self.world_map = WorldMap()
        self.pathfinding = PathfindingEngine(self.world_map, max_cost=pathfinding_max_cost) if enable_pathfinding else None

    # ---------- 回调 ----------
    def set_callback(self, name: str, cb: Callable) -> None:
        self._callbacks[name] = cb

    async def _fire(self, name: str, *args: Any) -> None:
        cb = self._callbacks.get(name)
        if cb:
            try:
                await cb(*args)
            except Exception:  # noqa: BLE001 回调异常不能拖垮主循环
                logger.exception("回调 %s 执行失败", name)

    # ---------- 连接 ----------
    async def connect(self) -> str | None:
        """执行登录并进入 play 状态；成功返回 None，失败返回错误信息字符串。"""
        try:
            self.conn = await TCPAsyncConnection.make_client((self.host, self.port), timeout=10)
            err = await self._login()
            if err:
                await self._close_conn()
                return err
            self.connected = True
            self._running.set()
            self._tasks.append(asyncio.create_task(self._play_loop()))
            # 启动动作队列 worker（阶段 1）
            if self.action_queue:
                await self.action_queue.start_worker()
            # 启动自主行为管理器（阶段 3）
            if self.behavior_manager:
                self._tasks.append(asyncio.create_task(self.behavior_manager.run()))
            await self._fire("on_ready")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("连接失败")
            await self._close_conn()
            return f"连接失败：{exc}"

    async def _login(self) -> str | None:
        conn = self.conn
        assert conn is not None
        # 1. 握手：登录状态
        buf = Buffer()
        buf.write_varint(0x00)  # Handshake
        buf.write_varint(PROTOCOL_VERSION)
        buf.write_utf(self.host)
        buf.write_value(StructFormat.USHORT, self.port)
        buf.write_varint(2)  # NextState.LOGIN
        await conn.write_bytearray(buf)

        # 2. LoginStart（1.20.1 格式：无 UUID）
        await conn.write_bytearray(LoginStart120(self.username).serialize())

        # 3. 登录态应答循环
        while True:
            data_buf = await self._read_raw()
            packet_id = data_buf.read_varint()
            if packet_id == 0x03:  # LoginSetCompression
                self.compression_threshold = data_buf.read_varint()
                self._compression_enabled = self.compression_threshold >= 0
                logger.info("服务器启用压缩，阈值=%s", self.compression_threshold)
            elif packet_id == 0x02:  # LoginSuccess
                # mcproto 读取 uuid + username，剩余（properties）忽略
                LoginSuccess.deserialize(data_buf)
                logger.info("登录成功：%s", self.username)
                # 1.20.1 没有 LoginAcknowledged；服务器收到 LoginSuccess 后自行进入 play 态。
                # 客户端设置（0x08）必须等收到第一个 play 包后再发，否则会抢跑被当作登录态包。
                return None
            elif packet_id == 0x00:  # LoginDisconnect
                reason = LoginDisconnect.deserialize(data_buf)
                return f"服务器拒绝登录：{reason.reason}"
            elif packet_id == 0x01:  # LoginEncryptionRequest
                return "服务器开启了正版验证（online-mode），机器人仅支持离线账号，无法登录。请在服务器配置中关闭正版验证。"
            elif packet_id == 0x04:  # LoginPluginRequest：直接回复空响应
                message_id = data_buf.read_varint()
                buf = Buffer()
                buf.write_varint(0x02)
                buf.write_varint(message_id)
                buf.write_value(StructFormat.BOOL, False)
                await conn.write_bytearray(buf)
            else:
                logger.warning("登录态收到未知包 0x%02x，跳过", packet_id)

    # ---------- 收发 ----------
    async def _send(self, packet: ServerBoundPacket) -> None:
        if self.conn is None:
            raise ConnectionError("未连接")
        await async_write_packet(self.conn, packet, compression_threshold=self.compression_threshold)

    async def _read_raw(self) -> Buffer:
        """读取一个 play 包原始缓冲区（已处理压缩头）。"""
        assert self.conn is not None
        data = await self.conn.read_bytearray()
        buf = Buffer(data)
        if self._compression_enabled:
            data_length = buf.read_varint()
            packet_data = buf.read(buf.remaining)
            if data_length != 0:
                import zlib

                buf = Buffer(zlib.decompress(packet_data))
            else:
                buf = Buffer(packet_data)
        return buf

    async def _close_conn(self) -> None:
        if self.conn is not None:
            try:
                await self.conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.conn = None
        self.connected = False
        self._running.clear()

    # ---------- 主循环 ----------
    async def _play_loop(self) -> None:
        logger.info("进入游戏主循环")
        while self.connected and not self._stopping:
            try:
                buf = await self._read_raw()
                packet_id = buf.read_varint()
                await self._handle_packet(packet_id, buf)
            except asyncio.CancelledError:
                break
            except (ConnectionError, OSError, EOFError) as exc:
                logger.warning("与服务器连接断开：%s", exc)
                await self._fire("on_disconnect", f"连接断开：{exc}", False)
                break
            except Exception:  # noqa: BLE001 个别包解析失败不致命
                logger.exception("处理客户端包 0x%02x 时出错", packet_id)
        if self._stopping:
            return
        self.connected = False
        self._running.clear()
        if self.reconnect_times > 0:
            await self._reconnect_loop()

    async def _reconnect_loop(self) -> None:
        delay = 3
        for attempt in range(1, self.reconnect_times + 1):
            if self._stopping:
                return
            logger.info("第 %s 次重连将在 %s 秒后尝试", attempt, delay)
            await asyncio.sleep(delay)
            if self._stopping:
                return
            err = await self.connect()
            if err is None:
                logger.info("重连成功")
                return
            logger.warning("重连失败：%s", err)
            delay = min(delay * 2, 60)

    async def _handle_packet(self, packet_id: int, buf: Buffer) -> None:
        handlers = {
            CB_KEEP_ALIVE: self._on_keep_alive,
            CB_POSITION: self._on_position,
            CB_SYSTEM_CHAT: self._on_system_chat,
            CB_PLAYER_CHAT: self._on_player_chat,
            CB_PROFILELESS_CHAT: self._on_profileless_chat,
            CB_UPDATE_HEALTH: self._on_health,
            CB_SET_CONTAINER_CONTENT: self._on_set_container_content,
            CB_SET_SLOT: self._on_set_slot,
            CB_PLAYER_INFO: self._on_player_info,
            CB_PLAYER_REMOVE: self._on_player_remove,
            CB_NAMED_ENTITY_SPAWN: self._on_named_entity_spawn,
            CB_REL_ENTITY_MOVE: self._on_rel_entity_move,
            CB_ENTITY_MOVE_LOOK: self._on_entity_move_look,
            CB_ENTITY_TELEPORT: self._on_entity_teleport,
            CB_ENTITY_DESTROY: self._on_entity_destroy,
            CB_KICK_DISCONNECT: self._on_kick,
            CB_PING: self._on_ping,
            CB_LOGIN: self._on_login,
            CB_RESPAWN: self._on_respawn,
            CB_SPAWN_POSITION: self._on_spawn_position,
        }
        handler = handlers.get(packet_id)
        if handler:
            await handler(buf)
        # 其余包（区块数据/物品/合成表等）不解析，直接跳过

    # ---------- 包处理 ----------
    async def _on_login(self, buf: Buffer) -> None:
        self.entity_id = buf.read_value(StructFormat.INT)
        # 已进入 play 态，补发客户端设置（之前不发送是为了等服务器完成状态切换）
        await self._send(SBTSettings())

    async def _on_respawn(self, buf: Buffer) -> None:
        # 死亡重生：重置位置等待服务器同步
        self.position = None

    async def _on_keep_alive(self, buf: Buffer) -> None:
        keep_id = buf.read_value(StructFormat.LONGLONG)
        await self._send(SBTKeepAlive(keep_id))

    async def _on_ping(self, buf: Buffer) -> None:
        ping_id = buf.read_value(StructFormat.INT)
        await self._send(SBTPong(ping_id))

    async def _on_position(self, buf: Buffer) -> None:
        x = buf.read_value(StructFormat.DOUBLE)
        y = buf.read_value(StructFormat.DOUBLE)
        z = buf.read_value(StructFormat.DOUBLE)
        self.yaw = buf.read_value(StructFormat.FLOAT)
        self.pitch = buf.read_value(StructFormat.FLOAT)
        flags = buf.read_value(StructFormat.BYTE)
        teleport_id = buf.read_varint()
        # flags：0x01=x 相对，0x02=y 相对，0x04=z 相对，0x08=yaw 相对，0x10=pitch 相对
        if self.position is None:
            rel_x, rel_y, rel_z = 0.0, 0.0, 0.0
        else:
            rel_x, rel_y, rel_z = self.position
        if flags & 0x01:
            x += rel_x
        if flags & 0x02:
            y += rel_y
        if flags & 0x04:
            z += rel_z
        self.position = (x, y, z)
        if teleport_id >= 0:
            await self._send(SBTTeleportConfirm(teleport_id))

    async def _on_health(self, buf: Buffer) -> None:
        self.health = buf.read_value(StructFormat.FLOAT)
        self.food = buf.read_varint()
        self.food_saturation = buf.read_value(StructFormat.FLOAT)
        await self._fire("on_health", self.health, self.food)

    def _read_slot(self, buf: Buffer) -> dict[str, Any] | None:
        """读取一个物品槽位数据（Slot 类型）。
        
        返回 None（空槽）或 {"item_id": int, "count": int, "nbt": bytes}
        """
        present = buf.read_value(StructFormat.BOOL)
        if not present:
            return None
        item_id = buf.read_varint()
        count = buf.read_value(StructFormat.BYTE)
        nbt_bytes = buf.read_bytearray()  # NBT 数据（暂不解析）
        return {"item_id": item_id, "count": count, "nbt": nbt_bytes}

    async def _on_set_container_content(self, buf: Buffer) -> None:
        """Set Container Content (0x11)：批量更新容器内容。
        
        window_id=0 是玩家自己的背包。
        """
        window_id = buf.read_value(StructFormat.UBYTE)
        state_id = buf.read_varint()
        count = buf.read_varint()
        slots = []
        for _ in range(count):
            slot_data = self._read_slot(buf)
            slots.append(slot_data)
        carried_item = self._read_slot(buf)  # 鼠标拖动的物品
        
        if window_id == 0:  # 玩家背包
            self.inventory.clear()
            for i, slot_data in enumerate(slots):
                if slot_data:
                    self.inventory[i] = slot_data
            logger.debug("库存已更新：%d 个非空槽位", len(self.inventory))

    async def _on_set_slot(self, buf: Buffer) -> None:
        """Set Slot (0x15)：单个槽位更新。"""
        window_id = buf.read_value(StructFormat.BYTE)
        state_id = buf.read_varint()
        slot_id = buf.read_value(StructFormat.SHORT)
        slot_data = self._read_slot(buf)
        
        if window_id == 0:  # 玩家背包
            if slot_data:
                self.inventory[slot_id] = slot_data
            else:
                self.inventory.pop(slot_id, None)

    async def _on_spawn_position(self, buf: Buffer) -> None:
        """接收出生点坐标（阶段 2 复合动作需要）"""
        # Position 编码为单个 i64：packed = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
        packed = buf.read_value(StructFormat.LONGLONG)
        x = (packed >> 38)
        y = (packed & 0xFFF)
        z = ((packed >> 12) & 0x3FFFFFF)
        # 符号扩展（26 位 x/z，12 位 y）
        if x >= (1 << 25):
            x -= (1 << 26)
        if y >= (1 << 11):
            y -= (1 << 12)
        if z >= (1 << 25):
            z -= (1 << 26)
        self.spawn_position = (float(x), float(y), float(z))
        logger.info("出生点坐标：%s", self.spawn_position)

    async def _on_system_chat(self, buf: Buffer) -> None:
        content = buf.read_utf()
        is_action_bar = buf.read_value(StructFormat.BOOL)
        if is_action_bar:
            return
        text = extract_chat_text(content)
        if text:
            await self._fire("on_chat", None, text)

    async def _on_profileless_chat(self, buf: Buffer) -> None:
        """Profileless Chat (0x1B)：无玩家档案的消息（控制台 /say、服务器公告等）。

        结构：message(string) / type(varint) / name(string) / target(可选 string)
        """
        message = extract_chat_text(buf.read_utf())
        buf.read_varint()  # type
        name = extract_chat_text(buf.read_utf())
        buf.read_optional(buf.read_utf)  # target
        if message:
            await self._fire("on_chat", name or None, message)

    async def _on_player_chat(self, buf: Buffer) -> None:
        sender_uuid = UUID.deserialize(buf)
        index = buf.read_varint()
        signature = buf.read_optional(buf.read_bytearray)
        plain_message = buf.read_utf()
        buf.read_value(StructFormat.LONGLONG)  # timestamp
        buf.read_value(StructFormat.LONGLONG)  # salt
        # previousMessages
        count = buf.read_varint()
        for _ in range(count):
            msg_id_type = buf.read_value(StructFormat.UBYTE)
            if msg_id_type == 0:
                buf.read(16)  # sender uuid
                buf.read_optional(buf.read_bytearray)  # signature
            else:
                buf.read_varint()
        unsigned_content = buf.read_optional(buf.read_utf)
        filter_type = buf.read_varint()
        if filter_type == 2:
            n = buf.read_varint()
            for _ in range(n):
                buf.read_value(StructFormat.LONGLONG)
        buf.read_varint()  # type
        network_name = buf.read_utf()
        buf.read_optional(buf.read_utf)  # network target name

        self._last_chat_index = max(self._last_chat_index, index)
        text = plain_message or unsigned_content or ""
        sender_name = self.players.get(str(sender_uuid)) or network_name or "玩家"
        if text:
            await self._fire("on_chat", sender_name, text)

    async def _on_player_info(self, buf: Buffer) -> None:
        action = buf.read_value(StructFormat.UBYTE)
        count = buf.read_varint()
        for _ in range(count):
            uuid = UUID.deserialize(buf)
            uuid_str = str(uuid)
            if action & 0x01:  # add_player
                name = buf.read_utf()
                props = buf.read_varint()
                for _ in range(props):
                    buf.read_utf()
                    buf.read_utf()
                    if buf.read_value(StructFormat.BOOL):
                        buf.read_utf()
                self.players[uuid_str] = name
            if action & 0x02:  # initialize_chat
                n = buf.read_varint()
                for _ in range(n):
                    buf.read(16)
                    buf.read_value(StructFormat.LONGLONG)
                    buf.read_value(StructFormat.LONGLONG)
                    buf.read_optional(buf.read_bytearray)
            if action & 0x04:  # update_game_mode
                buf.read_varint()
            if action & 0x08:  # update_listed
                buf.read_value(StructFormat.BOOL)
            if action & 0x10:  # update_latency
                buf.read_varint()
            if action & 0x20:  # update_display_name
                buf.read_optional(buf.read_utf)
        await self._fire("on_player_list", list(self.players.values()))

    async def _on_player_remove(self, buf: Buffer) -> None:
        count = buf.read_varint()
        for _ in range(count):
            uuid = UUID.deserialize(buf)
            self.players.pop(str(uuid), None)
        await self._fire("on_player_list", list(self.players.values()))

    async def _on_named_entity_spawn(self, buf: Buffer) -> None:
        entity_id = buf.read_varint()
        uuid = UUID.deserialize(buf)
        x = buf.read_value(StructFormat.DOUBLE)
        y = buf.read_value(StructFormat.DOUBLE)
        z = buf.read_value(StructFormat.DOUBLE)
        buf.read_value(StructFormat.BYTE)  # yaw
        buf.read_value(StructFormat.BYTE)  # pitch
        self.entities[entity_id] = {"uuid": str(uuid), "x": x, "y": y, "z": z}

    async def _on_rel_entity_move(self, buf: Buffer) -> None:
        entity_id = buf.read_varint()
        d_x = buf.read_value(StructFormat.SHORT) / 4096.0
        d_y = buf.read_value(StructFormat.SHORT) / 4096.0
        d_z = buf.read_value(StructFormat.SHORT) / 4096.0
        buf.read_value(StructFormat.BOOL)
        ent = self.entities.get(entity_id)
        if ent:
            ent["x"] += d_x
            ent["y"] += d_y
            ent["z"] += d_z

    async def _on_entity_move_look(self, buf: Buffer) -> None:
        entity_id = buf.read_varint()
        d_x = buf.read_value(StructFormat.SHORT) / 4096.0
        d_y = buf.read_value(StructFormat.SHORT) / 4096.0
        d_z = buf.read_value(StructFormat.SHORT) / 4096.0
        buf.read_value(StructFormat.BYTE)  # yaw
        buf.read_value(StructFormat.BYTE)  # pitch
        buf.read_value(StructFormat.BOOL)
        ent = self.entities.get(entity_id)
        if ent:
            ent["x"] += d_x
            ent["y"] += d_y
            ent["z"] += d_z

    async def _on_entity_teleport(self, buf: Buffer) -> None:
        entity_id = buf.read_varint()
        x = buf.read_value(StructFormat.DOUBLE)
        y = buf.read_value(StructFormat.DOUBLE)
        z = buf.read_value(StructFormat.DOUBLE)
        buf.read_value(StructFormat.BYTE)  # yaw
        buf.read_value(StructFormat.BYTE)  # pitch
        buf.read_value(StructFormat.BOOL)
        ent = self.entities.get(entity_id)
        if ent:
            ent["x"], ent["y"], ent["z"] = x, y, z

    async def _on_entity_destroy(self, buf: Buffer) -> None:
        count = buf.read_varint()
        for _ in range(count):
            entity_id = buf.read_varint()
            self.entities.pop(entity_id, None)

    async def _on_kick(self, buf: Buffer) -> None:
        reason = extract_chat_text(buf.read_utf())
        self.connected = False
        self._running.clear()
        await self._fire("on_disconnect", f"被服务器踢出：{reason}", True)

    # ---------- 对外动作 ----------
    async def send_chat(self, message: str) -> bool:
        """在游戏内发送一条聊天消息（未签名，适用于关闭正版验证的服务器）。"""
        if not self.connected:
            return False
        msg = message.strip()
        if not msg or len(msg) > 256:
            return False
        await self._send(
            SBTChatMessage(
                message=msg,
                timestamp=int(time.time() * 1000),
                salt=random.getrandbits(63),  # 有符号 i64 范围内的随机盐
                signature=None,
                offset=0,
                acknowledged=b"\x00\x00\x00",
            )
        )
        return True

    # ---------- 库存与物品 ----------
    def has_item(self, item_id: int) -> bool:
        """检查库存中是否有指定物品。"""
        return any(slot["item_id"] == item_id for slot in self.inventory.values())

    def count_item(self, item_id: int) -> int:
        """统计库存中指定物品的数量。"""
        return sum(slot["count"] for slot in self.inventory.values() if slot["item_id"] == item_id)

    def find_food_slot(self) -> int | None:
        """在快捷栏（0-8）找第一个食物，返回槽位 ID。
        
        常见食物 ID（1.20.1）：
        - 面包 393, 苹果 397, 熟猪排 424, 熟牛肉 423
        - 胡萝卜 427, 烤马铃薯 429, 曲奇 434
        - 西瓜片 436, 熟鸡肉 424, 烤兔肉 531
        """
        food_ids = {393, 397, 424, 423, 427, 429, 434, 436, 531, 320, 322, 297, 350, 360, 366, 391, 400}
        for slot_id in range(9):  # 只检查快捷栏
            slot_data = self.inventory.get(slot_id)
            if slot_data and slot_data["item_id"] in food_ids:
                return slot_id
        return None

    async def use_item(self, hand: int = 0) -> bool:
        """使用手持物品（吃食物、喝药水等）。
        
        Args:
            hand: 0=主手，1=副手
        
        Returns:
            True 如果成功发送使用指令
        """
        if not self.connected:
            return False
        await self._send(SBTUseItem(hand=hand, sequence=0))
        return True

    async def switch_held_slot(self, slot: int) -> bool:
        """切换手持物品栏位（0-8）。"""
        if not self.connected or not 0 <= slot <= 8:
            return False
        self.held_slot = slot
        await self._send(SBTHeldItemSlot(slot=slot))
        return True

    async def eat_food(self) -> str | None:
        """自动找食物并吃掉。成功返回 None，失败返回原因。"""
        if not self.connected:
            return "机器人未连接"
        
        food_slot = self.find_food_slot()
        if food_slot is None:
            return "快捷栏没有食物"
        
        # 切换到食物槽位
        if self.held_slot != food_slot:
            await self.switch_held_slot(food_slot)
            await asyncio.sleep(0.1)
        
        # 使用物品（吃）
        await self.use_item(hand=0)
        logger.info("正在吃食物（槽位 %d）", food_slot)
        return None

    # ---------- 移动与视角 ----------
    async def look_at(self, yaw: float, pitch: float) -> None:
        if not self.connected or self.position is None:
            return
        self.yaw = yaw % 360.0
        self.pitch = max(-90.0, min(90.0, pitch))
        await self._send(SBTLook(self.yaw, self.pitch))

    async def move_to(self, x: float, z: float, *, timeout: float = 60.0, use_pathfinding: bool = True) -> str | None:
        """移动到目标位置，可选启用智能寻路。成功返回 None，失败返回原因。
        
        启用寻路时（use_pathfinding=True 且距离 > 10 格），使用 A* 算法规划路径；
        寻路失败或距离较近时回退到直线移动。
        """
        if not self.connected or self.position is None:
            return "机器人未连接或尚未同步位置"
        
        cx, cy, cz = self.position
        direct_dist = math.hypot(x - cx, z - cz)
        
        # 如果启用寻路且直线距离较远，尝试寻路
        if use_pathfinding and self.pathfinding and direct_dist > 10.0:
            path = self.pathfinding.find_path((cx, cy, cz), (x, cy, z))
            if path:
                # 沿路径点依次移动
                per_waypoint_timeout = timeout / len(path) if len(path) > 0 else timeout
                for px, py, pz in path[1:]:  # 跳过起点
                    err = await self._move_direct(px, pz, timeout=per_waypoint_timeout)
                    if err:
                        return f"寻路移动失败：{err}"
                return None
            # 寻路失败，回退到直线移动
        
        # 直线移动
        return await self._move_direct(x, z, timeout=timeout)

    async def _move_direct(self, x: float, z: float, *, timeout: float = 60.0) -> str | None:
        """直线走到 (x, z)，保持服务器同步的高度；成功返回 None，失败返回原因。

        服务器对每个移动包校验位移（超过 ~0.25 格会报 "moved wrongly" 并橡皮筋弹回），
        因此按真实客户端节奏以 20Hz 发送、每包步进约 0.22 格；每步都从服务器最新
        同步的位置重新计算方向，地形起伏被弹回时也能继续走。
        """
        if not self.connected or self.position is None:
            return "机器人未连接或尚未同步位置"
        deadline = time.monotonic() + timeout
        while self.connected and time.monotonic() < deadline:
            cx, cy, cz = self.position
            dx, dz = x - cx, z - cz
            dist = math.hypot(dx, dz)
            if dist < 0.3:
                return None
            yaw = math.degrees(math.atan2(dx, dz))
            step_dist = min(MOVE_STEP, dist)
            nx = cx + dx / dist * step_dist
            nz = cz + dz / dist * step_dist
            await self._send(SBTPositionLook(nx, cy, nz, yaw, self.pitch))
            self.position = (nx, cy, nz)
            await asyncio.sleep(MOVE_TICK)
        return "移动超时" if self.connected else "连接已断开"


    async def mine(self, x: int, y: int, z: int, *, timeout: float = 30.0) -> str | None:
        """挖掘指定方块。成功返回 None，失败返回原因。"""
        if not self.connected or self.position is None:
            return "机器人未连接或尚未同步位置"
        bx, by, bz = self.position
        dist = math.sqrt((bx - x - 0.5) ** 2 + (by - y) ** 2 + (bz - z - 0.5) ** 2)
        if dist > REACH_DISTANCE:
            return f"目标方块距离 {dist:.1f} 格，超出可挖掘距离（{REACH_DISTANCE} 格）"
        face = self._face_toward(bx, by, bz, x, y, z)
        self._dig_sequence += 1
        # 面向目标方块
        yaw = math.degrees(math.atan2(x + 0.5 - bx, z + 0.5 - bz))
        pitch = math.degrees(math.atan2(y + 0.5 - by, math.hypot(x + 0.5 - bx, z + 0.5 - bz)))
        await self._send(SBTLook(yaw, pitch))
        self.yaw, self.pitch = yaw, pitch
        await self._send(SBTArmAnimation(0))
        await self._send(SBTBlockDig(DIG_START, x, y, z, face, self._dig_sequence))
        await asyncio.sleep(0.15)
        if not self.connected:
            return "连接已断开"
        await self._send(SBTBlockDig(DIG_FINISH, x, y, z, face, self._dig_sequence))
        return None

    async def place_block(self, x: int, y: int, z: int, *, timeout: float = 30.0) -> str | None:
        """在指定位置放置方块。成功返回 None，失败返回原因。
        
        注意：这需要手持可放置的方块物品。目前简化实现，默认使用主手当前物品。
        """
        if not self.connected or self.position is None:
            return "机器人未连接或尚未同步位置"
        bx, by, bz = self.position
        dist = math.sqrt((bx - x - 0.5) ** 2 + (by - y) ** 2 + (bz - z - 0.5) ** 2)
        if dist > REACH_DISTANCE:
            return f"目标位置距离 {dist:.1f} 格，超出可放置距离（{REACH_DISTANCE} 格）"
        
        # 计算放置面：通常在目标位置下方的方块顶部放置（face=1，即上表面）
        # 简化实现：在目标位置下方一格的上表面放置
        place_on_y = y - 1
        face = FACE_UP
        
        # 面向目标位置
        yaw = math.degrees(math.atan2(x + 0.5 - bx, z + 0.5 - bz))
        pitch = math.degrees(math.atan2(y + 0.5 - by, math.hypot(x + 0.5 - bx, z + 0.5 - bz)))
        await self._send(SBTLook(yaw, pitch))
        self.yaw, self.pitch = yaw, pitch
        
        # 发送放置方块包（在目标下方的上表面）
        await self._send(SBTArmAnimation(0))
        await self._send(SBTBlockPlace(
            hand=0,
            x=x,
            y=place_on_y,
            z=z,
            face=face,
            cursor_x=0.5,
            cursor_y=1.0,  # 点击上表面的顶部
            cursor_z=0.5,
            inside_block=False,
            sequence=0
        ))
        await asyncio.sleep(0.1)
        return None

    @staticmethod
    def _face_toward(bx: float, by: float, bz: float, x: int, y: int, z: int) -> int:
        """计算从方块 (x,y,z) 指向机器人方向的最近面（法线指向机器人的面）。"""
        center_x, center_y, center_z = x + 0.5, y + 0.5, z + 0.5
        dx, dy, dz = bx - center_x, by - center_y, bz - center_z
        adx, ady, adz = abs(dx), abs(dy), abs(dz)
        if adx >= ady and adx >= adz:
            return FACE_EAST if dx > 0 else FACE_WEST
        if ady >= adx and ady >= adz:
            return FACE_UP if dy > 0 else FACE_DOWN
        return FACE_SOUTH if dz > 0 else FACE_NORTH

    async def follow(self, player_name: str, *, timeout: float = 60.0) -> str | None:
        """跟随指定玩家（在其周围 2 格内停下）。成功返回 None，失败返回原因。"""
        if not self.connected:
            return "机器人未连接"
        target_uuid = next((u for u, n in self.players.items() if n.lower() == player_name.lower()), None)
        if target_uuid is None:
            return f"找不到玩家「{player_name}」，当前在线：{', '.join(self.players.values()) or '无人'}"
        target_entity = next((e for e in self.entities.values() if e.get("uuid") == target_uuid), None)
        if target_entity is None:
            return f"玩家「{player_name}」不在视野范围内"
        deadline = time.monotonic() + timeout
        while self.connected and time.monotonic() < deadline:
            target_entity = next((e for e in self.entities.values() if e.get("uuid") == target_uuid), None)
            if target_entity is None:
                return "目标丢失（可能离开视野）"
            tx, tz = target_entity["x"], target_entity["z"]
            if self.position is not None and math.hypot(tx - self.position[0], tz - self.position[2]) < 2.0:
                return None
            err = await self.move_to(tx, tz, timeout=min(5.0, deadline - time.monotonic()))
            if err and "超时" not in err:
                return err
        return "跟随超时"

    # ---------- 复合动作（阶段 2）----------
    async def move_and_mine(self, x: int, y: int, z: int, *, timeout: float = 90.0) -> str | None:
        """移动到方块附近（4格内）并挖掘它。成功返回 None，失败返回原因。"""
        if not self.connected or self.position is None:
            return "机器人未连接或尚未同步位置"
        
        bx, by, bz = self.position
        dist = math.sqrt((bx - x - 0.5) ** 2 + (by - y) ** 2 + (bz - z - 0.5) ** 2)
        
        # 如果距离超过挖掘范围，先移动接近
        if dist > REACH_DISTANCE:
            # 计算接近点（方块周围 3.5 格内，留出挖掘余地）
            direction_x = (bx - x - 0.5) / dist if dist > 0 else 0
            direction_z = (bz - z - 0.5) / dist if dist > 0 else 0
            approach_x = x + 0.5 + direction_x * 3.5
            approach_z = z + 0.5 + direction_z * 3.5
            
            move_timeout = max(10.0, timeout - 30.0)
            err = await self.move_to(approach_x, approach_z, timeout=move_timeout)
            if err:
                return f"移动失败：{err}"
        
        # 挖掘方块
        return await self.mine(x, y, z)

    async def collect_nearby_blocks(
        self, center_x: int, center_y: int, center_z: int, radius: int = 5, *, timeout: float = 180.0
    ) -> str | None:
        """收集中心点周围半径内的方块（螺旋扫描）。成功返回 None，失败返回原因或部分完成信息。"""
        if not self.connected:
            return "机器人未连接"
        
        # 生成螺旋扫描坐标列表
        blocks = []
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                for dy in range(-2, 3):  # Y方向±2格
                    if math.hypot(dx, dz) <= radius:
                        blocks.append((center_x + dx, center_y + dy, center_z + dz))
        
        # 按距离中心点排序（从近到远）
        blocks.sort(key=lambda b: math.hypot(b[0] - center_x, b[2] - center_z))
        
        collected = 0
        deadline = time.monotonic() + timeout
        
        for bx, by, bz in blocks:
            if time.monotonic() >= deadline:
                return f"超时，已收集 {collected}/{len(blocks)} 个方块"
            
            remaining_time = deadline - time.monotonic()
            err = await self.move_and_mine(bx, by, bz, timeout=min(30.0, remaining_time))
            if err is None:
                collected += 1
            elif "超时" in err or "连接" in err:
                return f"{err}，已收集 {collected}/{len(blocks)} 个方块"
            # 其他错误（如距离太远、方块不存在）继续尝试下一个
        
        return None if collected == len(blocks) else f"已收集 {collected}/{len(blocks)} 个方块"

    async def patrol_area(
        self, x1: float, z1: float, x2: float, z2: float, *, loops: int = 1
    ) -> str | None:
        """在矩形区域巡逻（走四个角点）。成功返回 None，失败返回原因。"""
        if not self.connected:
            return "机器人未连接"
        
        corners = [(x1, z1), (x2, z1), (x2, z2), (x1, z2)]
        
        for loop in range(loops):
            for i, (cx, cz) in enumerate(corners):
                err = await self.move_to(cx, cz)
                if err:
                    return f"巡逻第 {loop + 1}/{loops} 圈，第 {i + 1}/4 个角点时失败：{err}"
        
        return None

    async def return_to_spawn(self) -> str | None:
        """返回出生点。成功返回 None，失败返回原因。"""
        if not self.connected:
            return "机器人未连接"
        if self.spawn_position is None:
            return "未记录出生点坐标（服务器未发送 SpawnPosition 包）"
        
        sx, sy, sz = self.spawn_position
        return await self.move_to(sx, sz)

    # ---------- 状态 ----------
    def get_status(self) -> dict[str, Any]:
        pos = self.position
        return {
            "connected": self.connected,
            "server": f"{self.host}:{self.port}",
            "username": self.username,
            "position": [round(v, 2) for v in pos] if pos else None,
            "yaw": round(self.yaw, 1),
            "pitch": round(self.pitch, 1),
            "health": self.health,
            "food": self.food,
            "players": sorted(self.players.values()),
        }

    def player_names(self) -> list[str]:
        return sorted(self.players.values())

    async def disconnect(self) -> None:
        """优雅断开（不重连）。"""
        self._stopping = True
        # 停止自主行为管理器（阶段 3）
        if self.behavior_manager:
            self.behavior_manager.stop()
        # 停止动作队列 worker（阶段 1）
        if self.action_queue:
            await self.action_queue.stop_worker()
        if self.connected and self.conn is not None:
            try:
                await self._close_conn()
            except Exception:  # noqa: BLE001
                pass
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        self.connected = False
        self._running.clear()


@final
@define
class SBTLook(PlayServerBoundPacket):
    PACKET_ID = SB_LOOK

    yaw: float
    pitch: float

    def serialize_to(self, buf: Buffer) -> None:
        buf.write_value(StructFormat.FLOAT, self.yaw)
        buf.write_value(StructFormat.FLOAT, self.pitch)
        buf.write_value(StructFormat.BOOL, True)
