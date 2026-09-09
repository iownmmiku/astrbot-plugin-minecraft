"""测试单人存档导入为服务器世界的逻辑（无头）。"""

import asyncio
import importlib.util
import sys
import tempfile
import types
from pathlib import Path

_BASE = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _BASE)

# 世界导入逻辑不依赖协议库；用假模块占位，避免无头环境缺 cryptography 等依赖
def _fake_module(name: str):
    m = types.ModuleType(name)
    sys.modules.setdefault(name, m)
    return m

_mc = _fake_module("mcproto")
_buffer = _fake_module("mcproto.buffer")
_conn = _fake_module("mcproto.connection")
_handshake = _fake_module("mcproto.packets.handshaking.handshake")
_interactions = _fake_module("mcproto.packets.interactions")
_status = _fake_module("mcproto.packets.status.status")
_buffer.Buffer = object
_conn.TCPAsyncConnection = object
_handshake.Handshake = object
_handshake.NextState = object
_interactions.async_read_packet = object
_interactions.async_write_packet = object
_status.StatusRequest = object
_status.StatusResponse = object

# 把插件目录注册为包，使相对导入（from .java_manager import ...）可解析
_PKG = "astrbot_plugin_minecraft"
pkg = types.ModuleType(_PKG)
pkg.__path__ = [_BASE]
sys.modules[_PKG] = pkg
_jm_spec = importlib.util.spec_from_file_location(_PKG + ".java_manager", _BASE + "\\java_manager.py")
_jm = importlib.util.module_from_spec(_jm_spec)
sys.modules[_PKG + ".java_manager"] = _jm
_jm_spec.loader.exec_module(_jm)

from astrbot_plugin_minecraft.server_manager import LocalServerManager


async def test_world_import():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # 1. 造假单人存档
        fake_save = base / "saves" / "我的世界"
        (fake_save / "region").mkdir(parents=True)
        (fake_save / "level.dat").write_bytes(b"fake-level-dat")
        (fake_save / "region" / "r.0.0.mca").write_bytes(b"chunk-data")
        (fake_save / "session.lock").write_bytes(b"lock")  # 应被排除

        # 2. 造服务器管理器
        data_dir = base / "data"
        mgr = LocalServerManager(
            data_dir,
            version="1.20.1",
            port=25565,
            world_import_dir=str(fake_save),
            log_cb=lambda line: print("  [mgr]", line),
        )

        # 3. 首次导入
        print("=== 首次导入 ===")
        err = await mgr._import_world_if_needed()
        assert err is None, f"首次导入应成功，得到: {err}"
        world = data_dir / "server" / "world"
        assert (world / "level.dat").exists(), "level.dat 应已复制"
        assert (world / "region" / "r.0.0.mca").exists(), "区块文件应已复制"
        assert not (world / "session.lock").exists(), "session.lock 应被排除"
        print("✓ 存档已复制到服务器世界，session.lock 已排除")

        # 4. 原存档未被改动
        assert (fake_save / "level.dat").read_bytes() == b"fake-level-dat"
        assert (fake_save / "session.lock").exists()
        print("✓ 原存档未被改动")

        # 5. 第二次调用应跳过
        print("=== 第二次调用 ===")
        err2 = await mgr._import_world_if_needed()
        assert err2 is None
        print("✓ 世界已存在时跳过导入")

        # 6. 无效路径
        bad = LocalServerManager(data_dir, world_import_dir=str(base / "no-such-dir"))
        err3 = await bad._import_world_if_needed()
        assert err3 is not None and "不存在" in err3
        print("✓ 无效路径给出明确错误")


if __name__ == "__main__":
    asyncio.run(test_world_import())
    print("\n世界导入测试全部通过 ✅")
