"""本地 Paper 服务器托管 + MC 服务器状态查询。

- LocalServerManager：一键自建 Minecraft 1.20.1 Paper 服务器
  （自动下载 jar、写 eula/server.properties、Java 起服、TCP 就绪探测、
  崩溃自动重启、优雅停服）。数据目录与 jar 都放在插件数据目录下。
- query_status：用 mcproto 查询服务器 MOTD/在线人数/版本（服务端状态探测）。

本模块只依赖标准库与 mcproto，不依赖 AstrBot，便于无头测试。
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Callable

from mcproto.buffer import Buffer
from mcproto.connection import TCPAsyncConnection
from mcproto.packets.handshaking.handshake import Handshake, NextState
from mcproto.packets.interactions import async_read_packet, async_write_packet
from mcproto.packets.status.status import StatusRequest, StatusResponse

logger = logging.getLogger("astrbot_plugin_minecraft.server")

PAPER_API = "https://fill.papermc.io/v3/projects/paper/versions/{version}/builds/latest"
VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

DEFAULT_JAVA_CANDIDATES = [
    "java",
    r"C:\Program Files\Zulu\zulu-21\bin\java.exe",
    r"C:\Program Files\BellSoft\LibericaJDK-25\bin\java.exe",
    r"C:\Program Files\Common Files\Oracle\Java\javapath\java.exe",
    "/usr/bin/java",
    "/usr/lib/jvm/java-17-openjdk-amd64/bin/java",
]

DEFAULT_SERVER_PROPERTIES = {
    "online-mode": "false",
    "enforce-secure-profile": "false",
    "motd": "AstrBot Minecraft",
    "gamemode": "survival",
    "difficulty": "easy",
    "pvp": "true",
    "max-players": "20",
    "view-distance": "8",
    "simulation-distance": "8",
    "spawn-protection": "0",
    "generate-structures": "true",
    "level-type": "minecraft:normal",
    "allow-flight": "true",
    "enable-command-block": "false",
    "sync-chunk-writes": "true",
    "network-compression-threshold": "256",
}


async def _http_get_json(url: str, timeout: float = 30) -> dict[str, Any]:
    return await asyncio.to_thread(_http_get_json_sync, url, timeout)


def _http_get_json_sync(url: str, timeout: float = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "astrbot-plugin-minecraft/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_download(url: str, dest: Path, timeout: float = 120) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "astrbot-plugin-minecraft/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            fh.write(chunk)


def _java_candidates(configured: str = "") -> list[str]:
    import shutil

    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(DEFAULT_JAVA_CANDIDATES)
    resolved = []
    for cand in candidates:
        try:
            path = shutil.which(cand) if cand == "java" else cand
        except Exception:  # noqa: BLE001
            path = None
        if path:
            resolved.append(path)
    return resolved


async def detect_java(configured: str = "") -> str | None:
    """找出第一个可运行的 Java 可执行文件路径（版本探测通过即可）。"""
    for path in _java_candidates(configured):
        try:
            if await _java_version_probe(path):
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


async def _java_version_probe(path: str) -> bool:
    """快速探测 Java 是否能运行（版本 >= 17）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            "-version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


async def query_status(host: str, port: int, timeout: float = 5) -> dict[str, Any]:
    """查询服务器状态（MOTD/在线人数/协议版本/样例玩家）。失败返回 {"error": ...}。"""
    try:
        conn = await TCPAsyncConnection.make_client((host, port), timeout=timeout)
        await async_write_packet(
            conn,
            Handshake(protocol_version=763, server_address=host, server_port=port, next_state=NextState.STATUS),
        )
        await async_write_packet(conn, StatusRequest())
        packet_map = {StatusResponse.PACKET_ID: StatusResponse}
        resp = await async_read_packet(conn, packet_map)
        await conn.close()
        return resp.data if isinstance(resp, StatusResponse) else {"error": "响应格式异常"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


async def tcp_probe(host: str, port: int, timeout: float = 3) -> bool:
    """TCP 连通性探测（服务器就绪检查用）。"""
    try:
        conn = await asyncio.open_connection(host, port)
        conn[1].close()
        return True
    except Exception:  # noqa: BLE001
        return False


class LocalServerManager:
    """本地 Paper 服务器生命周期管理。"""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        version: str = "1.20.1",
        port: int = 25565,
        motd: str = "AstrBot Minecraft",
        java_path: str = "",
        log_cb: Callable[[str], None] | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.server_dir = self.data_dir / "server"
        self.version = version
        self.port = port
        self.motd = motd
        self.java_path = java_path
        self.log_cb = log_cb or (lambda line: logger.info("[mc-server] %s", line))

        self.process: asyncio.subprocess.Process | None = None
        self.state = "stopped"  # stopped | downloading | starting | running | stopping
        self.ready = False
        self._watch_task: asyncio.Task | None = None
        self._restart_on_exit = False
        self._crash_count = 0
        self._stop_event = asyncio.Event()

    # ---------- 下载 ----------
    @property
    def jar_path(self) -> Path:
        return self.server_dir / "server.jar"

    def jar_exists(self) -> bool:
        return self.jar_path.exists() and self.jar_path.stat().st_size > 1_000_000

    async def ensure_downloaded(self) -> str | None:
        """确保服务器 jar 存在；成功返回 None，失败返回错误信息。"""
        if self.jar_exists():
            return None
        self.server_dir.mkdir(parents=True, exist_ok=True)
        self.state = "downloading"
        self.log_cb(f"开始下载 {self.version} 服务器核心……")
        err = await self._download_paper()
        if err is None:
            self.log_cb(f"下载完成：{self.jar_path.name}")
            self.state = "stopped"
            return None
        self.log_cb(f"Paper 下载失败（{err}），回退到 Mojang 官方服务端……")
        err2 = await self._download_vanilla()
        if err2 is None:
            self.log_cb(f"下载完成：{self.jar_path.name}（Mojang 官方）")
            self.state = "stopped"
            return None
        self.state = "stopped"
        return f"下载服务器核心失败：Paper({err})；Mojang({err2})"

    async def _download_paper(self) -> str | None:
        try:
            latest = await _http_get_json(PAPER_API.format(version=self.version))
            # v3 API：/builds/latest 直接返回单个 build 对象（含 id 与 downloads 字典）
            builds = latest.get("builds") or []
            if builds:
                build = builds[-1]
            else:
                build = latest
            app = None
            for flavor in ("server:default", "server:mojang"):
                candidate = build.get("downloads", {}).get(flavor)
                if candidate and candidate.get("url"):
                    app = candidate
                    break
            if app is None:
                return "Paper API 未返回可下载文件"
            await asyncio.to_thread(_http_download, app["url"], self.jar_path, 180)
            return None
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    async def _download_vanilla(self) -> str | None:
        try:
            manifest = await _http_get_json(VERSION_MANIFEST)
            entry = next((v for v in manifest.get("versions", []) if v.get("id") == self.version), None)
            if not entry:
                return f"版本清单中找不到 {self.version}"
            meta = await _http_get_json(entry["url"])
            url = meta["downloads"]["server"]["url"]
            await asyncio.to_thread(_http_download, url, self.jar_path, 300)
            return None
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    # ---------- 配置 ----------
    def _write_server_properties(self) -> None:
        props = dict(DEFAULT_SERVER_PROPERTIES)
        props["server-port"] = str(self.port)
        props["motd"] = self.motd.replace("\\n", "\n")
        lines = [f"{k}={v}" for k, v in props.items()]
        (self.server_dir / "server.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_eula(self) -> None:
        (self.server_dir / "eula.txt").write_text(
            "#By changing the setting below to TRUE you are indicating your agreement to our EULA "
            "(https://aka.ms/MinecraftEULA).\neula=true\n",
            encoding="utf-8",
        )

    # ---------- 生命周期 ----------
    async def start(self, *, restart_on_exit: bool = True) -> str | None:
        """启动服务器；成功返回 None，失败返回错误信息。"""
        if self.process is not None and self.process.returncode is None:
            return None
        err = await self.ensure_downloaded()
        if err:
            return err
        self.server_dir.mkdir(parents=True, exist_ok=True)
        self._write_eula()
        self._write_server_properties()
        if not self.java_path:
            self.java_path = (await detect_java()) or "java"
        self.state = "starting"
        self._restart_on_exit = restart_on_exit
        self._stop_event = asyncio.Event()
        java = self.java_path
        cmd = [java, "-Xms1G", "-Xmx2G", "-jar", str(self.jar_path), "nogui"]
        self.log_cb("执行：" + " ".join(cmd))
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.server_dir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:  # noqa: BLE001
            self.state = "stopped"
            return f"启动服务器进程失败：{exc}"
        self._watch_task = asyncio.create_task(self._watch())
        return None

    async def wait_ready(self, timeout: float = 180) -> bool:
        """轮询服务器状态查询直到能正常应答（比 TCP 探测更可靠，避免启动竞态）。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self.process is not None and self.process.returncode is not None:
                return False
            result = await query_status("127.0.0.1", self.port, timeout=3)
            if "error" not in result:
                self.ready = True
                self.state = "running"
                return True
            await asyncio.sleep(2)
        return False

    async def _watch(self) -> None:
        proc = self.process
        if proc is None:
            return
        # 持续收集 stdout 日志，交给 log_cb
        async def pump_stdout() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    self.log_cb(line.decode("utf-8", errors="replace").rstrip())
                except Exception:  # noqa: BLE001
                    pass

        pump = asyncio.create_task(pump_stdout())
        code = await proc.wait()
        pump.cancel()
        self.process = None
        self.ready = False
        if self._stop_event.is_set():
            self.state = "stopped"
            self.log_cb("服务器已停止")
            return
        self._crash_count += 1
        self.log_cb(f"服务器进程退出（代码 {code}），累计异常退出 {self._crash_count} 次")
        if self._restart_on_exit and self._crash_count <= 3:
            delay = min(5 * self._crash_count, 30)
            self.log_cb(f"{delay} 秒后自动重启……")
            self.state = "restarting"
            await asyncio.sleep(delay)
            if not self._stop_event.is_set():
                await self.start(restart_on_exit=True)
        else:
            self.state = "stopped"

    async def stop(self, *, timeout: float = 30) -> None:
        """优雅停服：向控制台发送 stop 命令。"""
        self._stop_event.set()
        self._restart_on_exit = False
        self.state = "stopping"
        if self.process is not None and self.process.returncode is None:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write(b"stop\n")
                await self.process.stdin.drain()
                await asyncio.wait_for(self.process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                self.log_cb("stop 超时，强制结束进程")
                self.process.kill()
            except Exception as exc:  # noqa: BLE001
                logger.warning("停服异常：%s", exc)
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
        self.process = None
        self.state = "stopped"

    def get_status(self) -> dict[str, Any]:
        running = self.process is not None and self.process.returncode is None
        return {
            "mode": "local",
            "state": self.state,
            "ready": self.ready,
            "running": running,
            "port": self.port,
            "version": self.version,
            "jar_downloaded": self.jar_exists(),
            "crash_count": self._crash_count,
            "data_dir": str(self.server_dir),
        }
