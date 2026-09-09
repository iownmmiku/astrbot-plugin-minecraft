"""启动器 HTTP API 客户端
通过 HTTP API 远程控制 AstrBot Minecraft 启动器
"""

from __future__ import annotations

import logging
import urllib.request
import urllib.error
import json
from typing import Any, Optional

logger = logging.getLogger("astrbot_plugin_minecraft.launcher_api")


class LauncherAPIClient:
    """启动器 HTTP API 客户端"""
    
    def __init__(self, api_url: str, timeout: float = 10):
        """
        Args:
            api_url: 启动器 API 地址，例如 http://192.168.1.100:8765
            timeout: 请求超时时间（秒）
        """
        self.api_url = api_url.rstrip('/')
        self.timeout = timeout
    
    def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict[str, Any]:
        """发送 HTTP 请求"""
        url = f"{self.api_url}{path}"
        headers = {
            "User-Agent": "astrbot-plugin-minecraft/0.1",
            "Content-Type": "application/json"
        }
        
        try:
            if method == "GET":
                req = urllib.request.Request(url, headers=headers)
            else:
                body = json.dumps(data or {}).encode('utf-8')
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
            
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        
        except urllib.error.HTTPError as e:
            error_msg = e.read().decode('utf-8', errors='ignore')
            try:
                error_data = json.loads(error_msg)
                raise Exception(f"API Error: {error_data.get('error', error_msg)}")
            except json.JSONDecodeError:
                raise Exception(f"API Error {e.code}: {error_msg}")
        
        except urllib.error.URLError as e:
            raise Exception(f"Connection Error: {e.reason}")
        
        except Exception as e:
            raise Exception(f"Request Failed: {e}")
    
    def ping(self) -> bool:
        """检查 API 是否可访问"""
        try:
            result = self._request("GET", "/api/ping")
            return result.get("status") == "ok"
        except Exception as e:
            logger.warning(f"Launcher API ping failed: {e}")
            return False
    
    def get_status(self) -> dict[str, Any]:
        """获取服务器状态
        
        Returns:
            {
                "running": bool,
                "version": str or None,
                "players": int,
                "max_players": int,
                "cpu_percent": float,
                "memory_mb": int
            }
        """
        return self._request("GET", "/api/status")
    
    def get_versions(self) -> dict[str, Any]:
        """获取可用服务器版本列表
        
        Returns:
            {
                "versions": [
                    {"name": "vanilla-1.20.4", "type": "vanilla"},
                    ...
                ]
            }
        """
        return self._request("GET", "/api/versions")
    
    def start_server(self, version: Optional[str] = None) -> dict[str, Any]:
        """启动服务器
        
        Args:
            version: 服务器版本名，None 则使用当前选中的版本
        
        Returns:
            {"success": bool, "message": str}
        """
        data = {}
        if version:
            data["version"] = version
        return self._request("POST", "/api/server/start", data)
    
    def stop_server(self) -> dict[str, Any]:
        """停止服务器
        
        Returns:
            {"success": bool, "message": str}
        """
        return self._request("POST", "/api/server/stop")
    
    def send_command(self, command: str) -> dict[str, Any]:
        """发送服务器命令
        
        Args:
            command: 服务器命令（不带 /）
        
        Returns:
            {"success": bool, "message": str}
        """
        return self._request("POST", "/api/server/command", {"command": command})
    
    def download_version(self, version: str, server_type: str = "paper") -> dict[str, Any]:
        """下载服务器版本
        
        Args:
            version: 版本号，例如 "1.20.4"
            server_type: 服务器类型 "vanilla" 或 "paper"
        
        Returns:
            {"success": bool, "message": str}
        """
        return self._request("POST", "/api/versions/download", {
            "version": version,
            "type": server_type
        })
