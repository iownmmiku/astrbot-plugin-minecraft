"""调试：连接本地服并打印登录/进服的每一个包与压缩状态（不启动 bot 全流程）。"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = r"C:\Users\miku\.astrbot\data\site-packages"
for p in (ROOT, SITE):
    if p not in sys.path:
        sys.path.insert(0, p)

from bot_client import MCBot
from server_manager import LocalServerManager

PORT = 25567
DATA = os.path.join(ROOT, ".dbg_data")


async def main():
    mgr = LocalServerManager(DATA, version="1.20.1", port=PORT,
                             log_cb=lambda l: print("  [srv]", l))
    err = await mgr.start()
    if err:
        print("起服失败", err)
        return
    if not await mgr.wait_ready(timeout=240):
        print("服务器未就绪")
        await mgr.stop()
        return
    print("== 服务器就绪，开始连接 ==")

    bot = MCBot("127.0.0.1", PORT, "AstrBot", reconnect_times=0)
    bot.set_callback("on_disconnect", lambda reason, kicked: print(f"  !! on_disconnect: {reason} (kicked={kicked})"))
    bot.set_callback("on_chat", lambda s, t: print(f"  !! on_chat: {s}: {t}"))

    err = await bot.connect()
    print("connect 结果:", err)
    if err is None:
        print("connected =", bot.connected)
        await asyncio.sleep(6)
        print("6 秒后 connected =", bot.connected, "position =", bot.get_status()["position"])
    await bot.disconnect()
    await mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())
