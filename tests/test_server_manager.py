"""server_manager 无头单测：配置文件生成、jar 检测、状态机、状态查询。

不启动真实 Java 进程，用 mock 覆盖子进程与网络探测。
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, r"D:\工作台\astrbot_plugin_minecraft")

from server_manager import DEFAULT_SERVER_PROPERTIES, LocalServerManager, query_status, tcp_probe


class TestLocalServerManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.mgr = LocalServerManager(
            self.data,
            version="1.20.1",
            port=25566,
            motd="Test §aServer",
            java_path="fake-java",
            log_cb=lambda line: None,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        self.assertEqual(self.mgr.version, "1.20.1")
        self.assertEqual(self.mgr.port, 25566)
        self.assertEqual(self.mgr.state, "stopped")
        self.assertFalse(self.mgr.jar_exists())
        self.assertEqual(self.mgr.jar_path, self.data / "server" / "server.jar")

    def test_jar_exists(self):
        # 小文件不算有效 jar
        self.mgr.server_dir.mkdir(parents=True, exist_ok=True)
        self.mgr.jar_path.write_bytes(b"\x00" * 100)
        self.assertFalse(self.mgr.jar_exists())
        self.mgr.jar_path.write_bytes(b"\x00" * 2_000_000)
        self.assertTrue(self.mgr.jar_exists())

    def test_write_properties(self):
        self.mgr.server_dir.mkdir(parents=True, exist_ok=True)
        self.mgr._write_eula()
        self.mgr._write_server_properties()
        eula = (self.mgr.server_dir / "eula.txt").read_text(encoding="utf-8")
        self.assertIn("eula=true", eula)
        props = (self.mgr.server_dir / "server.properties").read_text(encoding="utf-8")
        self.assertIn("server-port=25566", props)
        self.assertIn("online-mode=false", props)
        self.assertIn("enforce-secure-profile=false", props)
        # motd 里的 § 颜色码应保留
        self.assertIn("Test §aServer", props)
        # 默认属性都应写入
        for key in DEFAULT_SERVER_PROPERTIES:
            self.assertIn(f"{key}=", props)

    def test_ensure_downloaded_skips_when_exists(self):
        self.mgr.server_dir.mkdir(parents=True, exist_ok=True)
        self.mgr.jar_path.write_bytes(b"\x00" * 2_000_000)
        with mock.patch.object(self.mgr, "_download_paper") as dp:
            err = asyncio.run(self.mgr.ensure_downloaded())
            dp.assert_not_called()
        self.assertIsNone(err)

    def test_download_paper_fallback_vanilla(self):
        # Paper 失败 -> 回退 Mojang；两者都失败返回错误
        with mock.patch.object(self.mgr, "_download_paper", return_value="paper down"), \
             mock.patch.object(self.mgr, "_download_vanilla", return_value=None):
            err = asyncio.run(self.mgr.ensure_downloaded())
        self.assertIsNone(err)

        with mock.patch.object(self.mgr, "_download_paper", return_value="paper down"), \
             mock.patch.object(self.mgr, "_download_vanilla", return_value="vanilla down"):
            err = asyncio.run(self.mgr.ensure_downloaded())
        self.assertIn("paper down", err)
        self.assertIn("vanilla down", err)

    def test_start_without_jar_runs_download(self):
        with mock.patch.object(self.mgr, "ensure_downloaded", return_value="下载失败"):
            err = asyncio.run(self.mgr.start())
        self.assertEqual(err, "下载失败")

    def test_download_paper_v3_parse(self):
        # Paper v3 API 返回单个 build 对象 + downloads 字典（server:default/server:mojang）
        self.mgr.server_dir.mkdir(parents=True, exist_ok=True)
        v3_response = {
            "id": 196,
            "channel": "STABLE",
            "downloads": {
                "server:mojang": {
                    "name": "paper-1.20.1-196-mojang.jar",
                    "url": "https://fill-data.papermc.io/v1/objects/abc/paper-1.20.1-196-mojang.jar",
                },
                "server:default": {
                    "name": "paper-1.20.1-196.jar",
                    "url": "https://fill-data.papermc.io/v1/objects/def/paper-1.20.1-196.jar",
                },
            },
        }
        downloaded: list[str] = []

        async def fake_get_json(url):
            return v3_response

        def fake_download(url, dest, timeout):
            downloaded.append(url)
            dest.write_bytes(b"\x00" * 2_000_000)

        with mock.patch("server_manager._http_get_json", fake_get_json), \
             mock.patch("server_manager._http_download", fake_download):
            err = asyncio.run(self.mgr._download_paper())
        self.assertIsNone(err)
        # 优先 server:default
        self.assertEqual(downloaded, ["https://fill-data.papermc.io/v1/objects/def/paper-1.20.1-196.jar"])

    def test_get_status(self):
        st = self.mgr.get_status()
        self.assertEqual(st["mode"], "local")
        self.assertEqual(st["port"], 25566)
        self.assertIn("state", st)
        self.assertIn("crash_count", st)


class TestStatusQuery(unittest.TestCase):
    def test_query_status_connection_refused(self):
        # 连不上的端口应返回 error，而不是抛异常
        result = asyncio.run(query_status("127.0.0.1", 1, timeout=2))
        self.assertIn("error", result)

    def test_tcp_probe_closed_port(self):
        self.assertFalse(asyncio.run(tcp_probe("127.0.0.1", 1, timeout=1)))


if __name__ == "__main__":
    unittest.main()
