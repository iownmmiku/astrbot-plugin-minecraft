"""chat_bridge 无头单测：订阅持久化与广播。"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, r"D:\工作台\astrbot_plugin_minecraft")

from chat_bridge import ChatBridge


class TestChatBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.pushed: list[tuple[str, str]] = []

        async def push(umo: str, text: str):
            self.pushed.append((umo, text))

        self.bridge = ChatBridge(self.data, push_cb=push)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_remove(self):
        self.assertTrue(self.bridge.add("s1"))
        self.assertFalse(self.bridge.add("s1"))  # 重复添加返回 False
        self.assertTrue(self.bridge.is_subscribed("s1"))
        self.assertTrue(self.bridge.remove("s1"))
        self.assertFalse(self.bridge.is_subscribed("s1"))
        self.assertFalse(self.bridge.remove("s1"))  # 重复移除返回 False

    def test_persist_across_instances(self):
        self.bridge.add("s1")
        self.bridge.add("s2")
        # 重新加载（模拟插件重载）
        bridge2 = ChatBridge(self.data, push_cb=lambda umo, text: None)
        self.assertTrue(bridge2.is_subscribed("s1"))
        self.assertTrue(bridge2.is_subscribed("s2"))
        self.assertEqual(bridge2.list_subscribers(), ["s1", "s2"])

    def test_broadcast_to_subscribers(self):
        self.bridge.add("s1")
        self.bridge.add("s2")
        n = asyncio.run(self.bridge.broadcast("【MC】Steve：hi"))
        self.assertEqual(n, 2)
        self.assertCountEqual(self.pushed, [("s1", "【MC】Steve：hi"), ("s2", "【MC】Steve：hi")])

    def test_broadcast_empty(self):
        n = asyncio.run(self.bridge.broadcast("hi"))
        self.assertEqual(n, 0)
        self.assertEqual(self.pushed, [])


if __name__ == "__main__":
    unittest.main()
