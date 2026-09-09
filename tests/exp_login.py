"""最小隔离实验：LoginSuccess 后，尝试不同的"下一个包"组合，观察服务器是否进入 play。

依次测试：
  MODE=ack    : 发 LoginAcknowledged(0x03)
  MODE=none   : 什么都不发，直接读
  MODE=settings: 直接发 ClientInformation(0x08)
每种模式下读服务器后续帧，看是 play 包还是踢人。
"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = r"C:\Users\miku\.astrbot\data\site-packages"
for p in (ROOT, SITE):
    if p not in sys.path:
        sys.path.insert(0, p)

from bot_client import LoginStart120, PROTOCOL_VERSION
from server_manager import LocalServerManager

PORT = 25570
DATA = os.path.join(ROOT, ".exp_data")
MODE = sys.argv[1] if len(sys.argv) > 1 else "ack"


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


async def main():
    mgr = LocalServerManager(DATA, version="1.20.1", port=PORT, log_cb=lambda l: None)
    err = await mgr.start()
    if err:
        print("起服失败", err)
        return
    if not await mgr.wait_ready(timeout=240):
        print("服务器未就绪")
        await mgr.stop()
        return
    print(f"== 服务器就绪，MODE={MODE} ==")

    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)

    def send_payload(payload: bytes):
        writer.write(varint(len(payload)) + payload)
        return payload

    # 握手 + LoginStart
    hb = bytearray()
    hb += varint(0x00) + varint(PROTOCOL_VERSION)
    host = "127.0.0.1"
    hb += varint(len(host)) + host.encode()
    hb += PORT.to_bytes(2, "big") + varint(2)
    send_payload(bytes(hb))
    send_payload(bytes(LoginStart120("AstrBot").serialize()))
    await writer.drain()

    compressed = False

    async def read_frame():
        b0 = await asyncio.wait_for(reader.readexactly(1), timeout=10)
        v = b0[0] & 0x7F
        sh = 7
        while b0[0] & 0x80:
            nxt = await asyncio.wait_for(reader.readexactly(1), timeout=10)
            b0 += nxt
            v |= (nxt[0] & 0x7F) << sh
            sh += 7
        data = await asyncio.wait_for(reader.readexactly(v), timeout=10)
        return bytes(b0) + data

    def read_varint_from(buf: bytearray, start: int):
        v = 0
        sh = 0
        i = start
        while True:
            b = buf[i]
            v |= (b & 0x7F) << sh
            i += 1
            sh += 7
            if not (b & 0x80):
                break
        return v, i

    # 登录态：帧1 SetCompression（无 data_length 前缀），帧2 LoginSuccess
    frame1 = await read_frame()
    bl = 1
    while frame1[bl - 1] & 0x80:
        bl += 1
    payload1 = frame1[bl:]
    pid1, _ = read_varint_from(bytearray(payload1), 0)
    print(f"帧1: len={len(frame1)} id=0x{pid1:02x} payload={' '.join(f'{b:02x}' for b in payload1)}", flush=True)
    if pid1 == 0x03:
        compressed = True
        print("  => SetCompression，启用压缩", flush=True)

    frame2 = await read_frame()
    bl = 1
    while frame2[bl - 1] & 0x80:
        bl += 1
    payload2 = frame2[bl:]
    dl2, i2 = read_varint_from(bytearray(payload2), 0)
    pid2, _ = read_varint_from(bytearray(payload2), i2)
    print(f"帧2: len={len(frame2)} data_length={dl2} id=0x{pid2:02x}", flush=True)
    if pid2 != 0x02:
        print("  非 LoginSuccess，退出")
        await mgr.stop()
        return

    # 按 MODE 发下一个包
    def send_next(name: str, payload_with_data_length: bool, body: bytes):
        if compressed and payload_with_data_length:
            full = varint(0) + body  # data_length=0 + 包体
        elif not compressed and not payload_with_data_length:
            full = body
        else:
            full = body
        print(f"发送 {name}: wire={' '.join(f'{b:02x}' for b in (varint(len(full)) + full))}", flush=True)
        send_payload(full)
        return full

    if MODE == "ack":
        send_next("LoginAcknowledged(0x03)", True, varint(0x03))
    elif MODE == "settings":
        body = bytearray(varint(0x08))
        loc = "zh_cn".encode()
        body += varint(len(loc)) + loc
        body += bytes([8, 0, 1, 0x7f, 0, 0, 1])
        send_next("ClientInformation(0x08)", True, bytes(body))
    # MODE == "none": 不发
    await writer.drain()

    # 读服务器后续 6 帧
    for n in range(6):
        try:
            fr = await read_frame()
            bl = 1
            while fr[bl - 1] & 0x80:
                bl += 1
            payload = fr[bl:]
            dl, i0 = read_varint_from(bytearray(payload), 0)
            pid, _ = read_varint_from(bytearray(payload), i0)
            first = " ".join(f"{b:02x}" for b in payload[:40])
            print(f"后续帧{n+1}: len={len(fr)} data_length={dl} id=0x{pid:02x} data={first}", flush=True)
        except asyncio.TimeoutError:
            print(f"后续帧{n+1}: 超时（服务器无响应）", flush=True)
            break
        except Exception as e:
            print(f"后续帧{n+1}: 异常 {e}", flush=True)
            break

    writer.close()
    await mgr.stop()
    print("== 结束 ==")


if __name__ == "__main__":
    asyncio.run(main())
