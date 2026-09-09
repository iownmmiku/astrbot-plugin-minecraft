"""完整转储：握手 -> LoginStart -> 读 SetCompression/LoginSuccess -> 发 LoginAcknowledged + SBTSettings -> dump 所有 play 帧。"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = r"C:\Users\miku\.astrbot\data\site-packages"
for p in (ROOT, SITE):
    if p not in sys.path:
        sys.path.insert(0, p)

from bot_client import LoginStart120, PROTOCOL_VERSION, SBTKeepAlive, SBTSettings, SBTTeleportConfirm
from server_manager import LocalServerManager

PORT = 25569
DATA = os.path.join(ROOT, ".dump2_data")


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
    print("== 服务器就绪 ==")

    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    COMPRESSED = False

    def frame(payload: bytes) -> bytes:
        return varint(len(payload)) + payload

    def send(payload: bytes):
        writer.write(frame(payload))
        return payload

    def dump(tag: str, raw: bytes):
        print(f"{tag} [{len(raw)}]: " + " ".join(f"{b:02x}" for b in raw[:48]) +
              (" ..." if len(raw) > 48 else ""))

    # 握手 + LoginStart
    hb = bytearray()
    hb += varint(0x00) + varint(PROTOCOL_VERSION)
    host = "127.0.0.1"
    hb += varint(len(host)) + host.encode()
    hb += PORT.to_bytes(2, "big") + varint(2)
    send(bytes(hb))
    send(bytes(LoginStart120("AstrBot").serialize()))
    await writer.drain()

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
        return v, bytes(b0) + data

    def decode_payload(payload: bytes):
        """解出 (data_length, packet_id, rest)。"""
        i = 0
        dl = 0
        sh = 0
        while True:
            b = payload[i]
            dl |= (b & 0x7F) << sh
            i += 1
            sh += 7
            if not (b & 0x80):
                break
        pid = 0
        sh = 0
        while True:
            b = payload[i]
            pid |= (b & 0x7F) << sh
            i += 1
            sh += 7
            if not (b & 0x80):
                break
        return dl, pid, payload[i:]

    # 登录态
    for n in range(3):
        flen, raw = await read_frame()
        payload = raw[flen.bit_length() and 0:]  # 帧内容（不含帧长 varint）
        # 去掉帧长 varint 字节
        bl = 1
        x = raw[0]
        while x & 0x80:
            x = raw[bl]
            bl += 1
        payload = raw[bl:]
        dl, pid, rest = decode_payload(payload)
        print(f"登录帧{n+1} len={flen}: id=0x{pid:02x} data_length={dl}", flush=True)
        if pid == 0x03:
            COMPRESSED = True
            print("  => 启用压缩", flush=True)
        if pid == 0x02:
            print("  => LoginSuccess，发送 LoginAcknowledged", flush=True)
            # LoginAcknowledged（压缩格式）
            if COMPRESSED:
                send(varint(0) + varint(0x03))
            else:
                send(varint(0x03))
            await writer.drain()
            print("  => 发送 SBTSettings", flush=True)
            if COMPRESSED:
                # 手写 0x08 client info，与 bot_client.SBTSettings 同结构
                p = bytearray()
                p += varint(0x08)
                loc = "zh_cn".encode()
                p += varint(len(loc)) + loc
                p += bytes([8, 0, 1, 0x7f, 0, 0, 1])
                send(bytes(p))
            else:
                p = bytearray()
                p += varint(0x08)
                loc = "zh_cn".encode()
                p += varint(len(loc)) + loc
                p += bytes([8, 0, 1, 0x7f, 0, 0, 1])
                send(bytes(p))
            await writer.drain()
            print("  => 已发送，开始读 play 帧", flush=True)

    # play 态：读 8 帧
    for n in range(8):
        try:
            flen, raw = await read_frame()
            bl = 1
            x = raw[0]
            while x & 0x80:
                x = raw[bl]
                bl += 1
            payload = raw[bl:]
            dl, pid, rest = decode_payload(payload)
            print(f"play帧{n+1} len={flen}: id=0x{pid:02x} data_length={dl} rest={' '.join(f'{b:02x}' for b in rest[:24])}", flush=True)
            # 应答 keep_alive (0x23) 与 position (0x3c)
            if pid == 0x23:
                ka = rest[:8]
                p = bytearray(varint(0x12)) + ka
                send(bytes(p))
                print("  => 回 keep_alive", flush=True)
                await writer.drain()
            if pid == 0x3c:
                tid = rest[-1]  # 简化：假设最后是 teleportId varint 单字节
                p = bytearray(varint(0x00)) + varint(tid)
                send(bytes(p))
                print(f"  => 回 teleport_confirm({tid})", flush=True)
                await writer.drain()
        except asyncio.TimeoutError:
            print(f"play帧{n+1} 超时", flush=True)
            break
        except Exception as e:
            print(f"play帧{n+1} 异常: {e}", flush=True)
            break

    await asyncio.sleep(2)
    writer.close()
    await mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())
