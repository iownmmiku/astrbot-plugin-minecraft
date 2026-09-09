"""bot_client 无头单测：包序列化、坐标打包、聊天文本提取、朝向计算。

运行方式（项目根目录）：
  D:\\AstrBot\\backend\\python\\python.exe tests\\run_tests.py
  # 或任意带 mcproto 的 Python： py -m unittest discover -s tests
"""

import sys
import unittest

sys.path.insert(0, r"D:\工作台\astrbot_plugin_minecraft")

from mcproto.buffer import Buffer
from mcproto.packets.interactions import _serialize_packet
from mcproto.protocol.base_io import StructFormat

from bot_client import (
    FACE_DOWN,
    FACE_EAST,
    FACE_NORTH,
    FACE_SOUTH,
    FACE_UP,
    FACE_WEST,
    LoginStart120,
    MCBot,
    SBTBlockDig,
    SBTChatMessage,
    SBTKeepAlive,
    SBTSettings,
    extract_chat_text,
    pack_position,
    unpack_position,
)


class TestPositionPack(unittest.TestCase):
    def test_round_trip(self):
        cases = [(1, 2, 3), (-1, 64, 100), (1000, -64, -2000), (0, 0, 0), (30000000, 255, -30000000)]
        for x, y, z in cases:
            self.assertEqual(unpack_position(pack_position(x, y, z)), (x, y, z))

    def test_bit_layout(self):
        # 验证 X/Z/Y 占位：1<<38 是 x 的最低位
        v = pack_position(1, 0, 0)
        self.assertEqual(v, 1 << 38)
        v = pack_position(0, 1, 0)
        self.assertEqual(v, 1)
        v = pack_position(0, 0, 1)
        self.assertEqual(v, 1 << 12)


class TestChatText(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(extract_chat_text("hello"), "hello")

    def test_text_field(self):
        self.assertEqual(extract_chat_text('{"text":"hello"}'), "hello")

    def test_translate_with_args(self):
        # 玩家聊天 translate 键被翻译成 <名字> 消息
        self.assertEqual(
            extract_chat_text('{"translate":"chat.type.text","with":[{"text":"Steve"},"hi"]}'),
            "<Steve> hi",
        )

    def test_joined_left_translate(self):
        self.assertEqual(
            extract_chat_text('{"translate":"multiplayer.player.joined","with":[{"text":"AstrBot"}]}'),
            "AstrBot 加入了游戏",
        )

    def test_extra(self):
        self.assertEqual(extract_chat_text('{"extra":[{"text":"a"},{"text":"b"}],"text":""}'), "ab")

    def test_invalid_json_fallback(self):
        self.assertEqual(extract_chat_text("{not json"), "{not json")


class TestPacketSerialize(unittest.TestCase):
    def _ser(self, pkt, threshold=-1):
        return bytes(_serialize_packet(pkt, compression_threshold=threshold))

    def test_keep_alive(self):
        buf = Buffer(self._ser(SBTKeepAlive(123456789)))
        self.assertEqual(buf.read_varint(), 0x12)
        self.assertEqual(buf.read_value(StructFormat.LONGLONG), 123456789)

    def test_chat_message(self):
        pkt = SBTChatMessage(message="你好", timestamp=1700000000000, salt=42)
        buf = Buffer(self._ser(pkt))
        self.assertEqual(buf.read_varint(), 0x05)
        self.assertEqual(buf.read_utf(), "你好")
        self.assertEqual(buf.read_value(StructFormat.LONGLONG), 1700000000000)
        self.assertEqual(buf.read_value(StructFormat.LONGLONG), 42)
        self.assertIsNone(buf.read_optional(buf.read_bytearray))

    def test_block_dig(self):
        pkt = SBTBlockDig(status=0, x=10, y=64, z=-5, face=1, sequence=3)
        buf = Buffer(self._ser(pkt))
        self.assertEqual(buf.read_varint(), 0x1D)
        self.assertEqual(buf.read_varint(), 0)
        self.assertEqual(unpack_position(buf.read_value(StructFormat.LONGLONG)), (10, 64, -5))
        self.assertEqual(buf.read_value(StructFormat.BYTE), 1)
        self.assertEqual(buf.read_varint(), 3)

    def test_settings(self):
        buf = Buffer(self._ser(SBTSettings()))
        self.assertEqual(buf.read_varint(), 0x08)
        self.assertEqual(buf.read_utf(), "zh_cn")

    def test_login_start_120(self):
        buf = Buffer(bytes(LoginStart120("AstrBot").serialize()))
        self.assertEqual(buf.read_varint(), 0)
        self.assertEqual(buf.read_utf(), "AstrBot")
        self.assertFalse(buf.read_value(StructFormat.BOOL))

    def test_compression(self):
        import zlib

        big = self._ser(SBTChatMessage(message="x" * 500, timestamp=1, salt=2), threshold=128)
        buf = Buffer(big)
        data_len = buf.read_varint()
        data = buf.read(buf.remaining)
        un = zlib.decompress(data) if data_len else data
        self.assertEqual(Buffer(un).read_varint(), 0x05)


class TestFaceToward(unittest.TestCase):
    def test_faces(self):
        # 机器人相对方块位置 -> 应挖掘指向机器人的那个面
        self.assertEqual(MCBot._face_toward(0, 0, 0, 1, 0, 0), FACE_WEST)  # 机器人在西侧
        self.assertEqual(MCBot._face_toward(5, 0, 0, 1, 0, 0), FACE_EAST)  # 机器人在东侧
        self.assertEqual(MCBot._face_toward(0, 0, 0, 0, 1, 0), FACE_DOWN)  # 机器人在下方
        self.assertEqual(MCBot._face_toward(0, 10, 0, 0, 1, 0), FACE_UP)  # 机器人在上方
        self.assertEqual(MCBot._face_toward(0, 0, -5, 0, 0, 1), FACE_NORTH)  # 机器人在北侧
        self.assertEqual(MCBot._face_toward(0, 0, 5, 0, 0, 1), FACE_SOUTH)  # 机器人在南侧


class TestPlayerChatParsing(unittest.TestCase):
    """用按 1.20.1 协议构造的字节流验证 player_chat 解析。"""

    @staticmethod
    def _build_player_chat(uuid_bytes: bytes, plain: str, sender_name_network: str,
                           unsigned: str | None = None) -> bytes:
        buf = Buffer()
        buf.write_varint(0x35)
        buf.write(uuid_bytes)  # senderUuid
        buf.write_varint(5)  # index
        buf.write_value(StructFormat.BOOL, False)  # 无签名
        buf.write_utf(plain)  # plainMessage
        buf.write_value(StructFormat.LONGLONG, 1700000000000)  # timestamp
        buf.write_value(StructFormat.LONGLONG, 7)  # salt
        buf.write_varint(0)  # previousMessages 数量
        buf.write_optional(unsigned, buf.write_utf)  # unsignedChatContent
        buf.write_varint(0)  # filterType
        buf.write_varint(1)  # type=chat
        buf.write_utf(sender_name_network)  # networkName
        buf.write_value(StructFormat.BOOL, False)  # 无 networkTargetName
        return bytes(buf)

    def test_parse_signed_plain_message(self):
        # 签名消息：内容在 plainMessage
        uuid_bytes = bytes(range(16))
        data = self._build_player_chat(uuid_bytes, "hi there", "Steve")
        self._run_and_assert(uuid_bytes, Buffer(data), expected="hi there")

    def test_parse_unsigned_chat_content(self):
        # 未签名消息（关闭正版验证的服）：内容在 unsignedChatContent
        uuid_bytes = bytes(range(16))
        data = self._build_player_chat(uuid_bytes, "", "Steve", unsigned="你好呀")
        self._run_and_assert(uuid_bytes, Buffer(data), expected="你好呀")

    def _run_and_assert(self, uuid_bytes, buf, expected):
        import asyncio
        import uuid as uuidlib

        bot = MCBot("localhost", 1, "AstrBot")
        bot.players[str(uuidlib.UUID(bytes=uuid_bytes))] = "Steve"
        received = {}

        async def run():
            async def cb(sender, text):
                received["sender"], received["text"] = sender, text

            bot.set_callback("on_chat", cb)
            await bot._on_player_chat(buf)

        asyncio.run(run())
        self.assertEqual(received.get("sender"), "Steve")
        self.assertEqual(received.get("text"), expected)


if __name__ == "__main__":
    unittest.main()
