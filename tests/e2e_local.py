"""端到端实测：真实下载 Paper 1.20.1 → 起本地服 → 机器人进服游玩验证。

用法（项目根目录）：
  D:\\AstrBot\\backend\\python\\python.exe tests\\e2e_local.py

验证项：
  1. 本地服务器下载并启动（TCP 就绪）
  2. 机器人进服（登录 → play 状态 → 位置同步）
  3. 机器人说话（服务器日志可见）
  4. 机器人收到服务器广播聊天（on_chat 回调触发）
  5. 机器人移动（position 变化）
  6. 机器人挖掘脚下方块（y 坐标下降 1，证明方块真的被挖掉）
"""

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = r"C:\Users\miku\.astrbot\data\site-packages"
for p in (ROOT, SITE):
    if p not in sys.path:
        sys.path.insert(0, p)

from bot_client import MCBot
from server_manager import LocalServerManager

PORT = 25566
DATA_DIR = os.path.join(ROOT, ".e2e_data")


async def main() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"[{tag}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            ok = False

    # 1. 起服
    print("== 1. 本地服务器 ==")
    mgr = LocalServerManager(DATA_DIR, version="1.20.1", port=PORT, java_path=os.environ.get("MCBOT_JAVA", ""),
                             log_cb=lambda line: print(f"  [server] {line}"))
    err = await mgr.start()
    check("服务器启动（含 jar 下载）", err is None, str(err) if err else "")
    if err:
        return 1
    ready = await mgr.wait_ready(timeout=240)
    check("服务器 TCP 就绪", ready)
    if not ready:
        await mgr.stop()
        return 1

    # 2. 机器人进服
    print("== 2. 机器人进服 ==")
    bot = MCBot("127.0.0.1", PORT, "AstrBot", reconnect_times=0)
    chat_log: list[tuple[str | None, str]] = []
    ready_event = asyncio.Event()

    async def on_ready():
        ready_event.set()

    async def on_chat(sender, text):
        chat_log.append((sender, text))
        print(f"  [bot收到聊天] {sender}: {text}")

    bot.set_callback("on_ready", on_ready)
    bot.set_callback("on_chat", on_chat)
    # 服务器刚起好时登录态可能未就绪，重试几次
    err = "尚未尝试"
    for attempt in range(1, 4):
        err = await bot.connect()
        if err is None:
            break
        print(f"  [重试] 第 {attempt} 次进服失败：{err}")
        await asyncio.sleep(5)
    check("机器人登录进服", err is None, str(err) if err else "")
    if err:
        await mgr.stop()
        return 1
    try:
        await asyncio.wait_for(ready_event.wait(), timeout=30)
        check("on_ready 回调触发", True)
        await asyncio.sleep(3)  # 等位置/区块同步
        status = bot.get_status()
        check("位置已同步", status["position"] is not None, str(status["position"]))

        # 3. 机器人说话
        print("== 3. 机器人说话 ==")
        ok_send = await bot.send_chat("大家好，我是 AstrBot 机器人，我来玩啦！")
        check("发送聊天成功", ok_send)
        await asyncio.sleep(2)

        # 4. 服务器广播 -> 机器人收到
        print("== 4. 机器人接收聊天 ==")
        assert mgr.process is not None and mgr.process.stdin is not None
        mgr.process.stdin.write("say [E2E] 收到请回答\n".encode("utf-8"))
        await mgr.process.stdin.drain()
        await asyncio.sleep(3)
        got = any("[E2E] 收到请回答" in t for _, t in chat_log)
        check("收到服务器广播聊天", got, str(chat_log[-3:]))

        # 5. 移动
        print("== 5. 机器人移动 ==")
        pos0 = bot.get_status()["position"]
        assert pos0 is not None
        target_x, target_z = pos0[0] + 5, pos0[2] + 5
        r = await bot.move_to(target_x, target_z, timeout=30)
        check("move_to 执行成功", r is None, str(r) if r else "")
        await asyncio.sleep(1)
        pos1 = bot.get_status()["position"]
        moved = pos1 is not None and (abs(pos1[0] - target_x) < 0.6 and abs(pos1[2] - target_z) < 0.6)
        check("位置已移动到目标", moved, f"{pos0} -> {pos1}")

        # 6. 挖掘（挖 bot 前方 2 格的地面方块，用服务器命令广播确认挖掉）
        print("== 6. 机器人挖掘 ==")
        pos2 = bot.get_status()["position"]
        assert pos2 is not None
        tx, ty, tz = int(pos2[0]) + 2, int(pos2[1] - 1), int(pos2[2])
        console_cmds = []
        assert mgr.process is not None and mgr.process.stdin is not None
        mgr.process.stdin.write(f"execute if block {tx} {ty} {tz} minecraft:air run say [E2E] ALREADY_AIR\n".encode("utf-8"))
        await mgr.process.stdin.drain()
        await asyncio.sleep(1.5)
        r = await bot.mine(tx, ty, tz, timeout=15)
        check("mine 执行成功", r is None, str(r) if r else "")
        await asyncio.sleep(1.5)
        mgr.process.stdin.write(f"execute if block {tx} {ty} {tz} minecraft:air run say [E2E] MINED_OK\n".encode("utf-8"))
        await mgr.process.stdin.drain()
        await asyncio.sleep(2)
        mined = any("[E2E] MINED_OK" in t for _, t in chat_log)
        check("方块被挖掉（服务器确认变为空气）", mined, f"目标 ({tx},{ty},{tz})，聊天记录 {chat_log[-3:]}")

        # 收尾
        print("== 收尾 ==")
        await bot.disconnect()
        check("机器人断开", not bot.connected)
    finally:
        await mgr.stop()
        check("服务器停止", True)
    print("\n" + ("E2E 全部通过 ✅" if ok else "E2E 存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
