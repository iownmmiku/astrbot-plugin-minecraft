"""原始字节转储：不做任何解析，直接 dump 服务器在登录流程中发送的每个 TCP 帧。

用 asyncio 原始流：握手 -> LoginStart -> 读服务器发来的所有数据并 hexdump。
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

PORT = 25568
DATA = os.path.join(ROOT, ".dump_data")


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
    mgr = LocalServerManager(DATA, version="1.20.1", port=PORT,
                             log_cb=lambda l: None)
    err = await mgr.start()
    if err:
        print("起服失败", err)
        return
    if not await mgr.wait_ready(timeout=240):
        print("服务器未就绪")
        await mgr.stop()
        return
    print("== 服务器就绪，开始原始转储 ==")

    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)

    # 握手
    hb = bytearray()
    hb += varint(0x00)
    hb += varint(PROTOCOL_VERSION)
    host = "127.0.0.1"
    hb += varint(len(host)) + host.encode()
    hb += (PORT).to_bytes(2, "big")  # USHORT 大端网络序
    hb += varint(2)
    writer.write(varint(len(hb)) + bytes(hb))
    await writer.drain()

    # LoginStart（带长度前缀）
    ls = bytes(LoginStart120("AstrBot").serialize())
    writer.write(varint(len(ls)) + ls)
    await writer.drain()

    def dump(tag: str, data: bytes):
        print(f"{tag}: " + " ".join(f"{b:02x}" for b in data[:60]) +
              (" ..." if len(data) > 60 else f" (len={len(data)})"))

    # 读服务器发来的每个帧并 dump（带长度前缀，含 frame_len）
    for i in range(12):
        try:
            flen = await asyncio.wait_for(reader.readexactly(1), timeout=10)
            # 读 varint frame_len
            flen_val = flen[0] & 0x7F
            shift = 7
            while flen[0] & 0x80:
                nxt = await asyncio.wait_for(reader.readexactly(1), timeout=10)
                flen += nxt
                flen_val |= (nxt[0] & 0x7F) << shift
                shift += 7
            data = await asyncio.wait_for(reader.readexactly(flen_val), timeout=10)
            dump(f"帧{i+1} (len={flen_val})", bytes(flen) + data)
            # 简单解码：如果带 data_length 前缀
            payload = data
            dl = payload[0] & 0x7F
            sh = 7
            j = 1
            while payload[j-1] & 0x80:
                dl |= (payload[j] & 0x7F) << sh
                sh += 7
                j += 1
            pid = payload[j] & 0x7F
            sh2 = 7
            k = j + 1
            while payload[k-1] & 0x80:
                pid |= (payload[k] & 0x7F) << sh2
                sh2 += 7
                k += 1
            print(f"     => data_length={dl}, packet_id=0x{pid:02x}")
        except asyncio.TimeoutError:
            print(f"读取帧{i+1}超时")
            break
        except Exception as e:
            print(f"帧{i+1}读取异常: {e}")
            break

    writer.close()
    await mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())
