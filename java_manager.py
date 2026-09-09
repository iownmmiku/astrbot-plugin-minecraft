"""Java 自动检测和版本管理模块。

参考 PCL (Plain Craft Launcher) 的实现逻辑：
- 自动搜索系统中所有可用的 Java 安装
- 根据 Minecraft 版本自动确定所需 Java 版本
- 自动选择最合适的 Java（满足最低要求且版本不过高）
- 支持手动指定 Java 路径
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class JavaEntry:
    """Java 安装条目。"""
    
    def __init__(self, path: str, version: int = 0):
        self.path = path
        self.version = version  # 主版本号，如 8, 17, 21
    
    def __repr__(self):
        return f"JavaEntry(path={self.path}, version={self.version})"


class JavaManager:
    """Java 检测和管理器。"""
    
    def __init__(self):
        self._cache: list[JavaEntry] = []
    
    def get_mc_version_int(self, version_name: str) -> int:
        """获取 MC 版本的数字化版本号。
        
        例如：
        - 1.18.1 -> 11801
        - 1.7.10 -> 1710
        - 1.20 -> 12000
        
        Returns:
            版本整数，0 表示无法解析
        """
        try:
            # 处理快照版本（如 23w51a）
            if 'w' in version_name:
                year = int(version_name.split('w')[0])
                if year >= 24:
                    return 12100  # 1.21
                elif year >= 23:
                    return 12000  # 1.20
                elif year >= 22:
                    return 11900  # 1.19
                elif year >= 21:
                    return 11800  # 1.18
                elif year >= 20:
                    return 11700  # 1.17
            
            # 处理远古版本（Beta, Alpha, inf, c, rd）
            if any(version_name.startswith(prefix) for prefix in ['b', 'a', 'inf', 'c', 'rd']):
                return 800
            
            # 处理正式版本
            parts = version_name.split('.')
            if len(parts) < 2:
                return 0
            
            major = int(parts[0])
            minor = int(parts[1])
            patch = int(parts[2]) if len(parts) >= 3 else 0
            
            return major * 10000 + minor * 100 + patch
        
        except (ValueError, IndexError):
            logger.warning(f"无法解析 MC 版本号：{version_name}")
            return 0
    
    def get_java_requirement(self, mc_version: str) -> int:
        """根据 MC 版本确定所需 Java 版本。
        
        版本对应关系：
        - MC 1.20.5+ -> Java 21
        - MC 1.18+ -> Java 17
        - MC 1.17 -> Java 16
        - MC 1.12+ -> Java 8
        - MC 更早 -> Java 8
        
        Returns:
            Java 主版本号（8, 16, 17, 21），0 表示无法确定
        """
        version_int = self.get_mc_version_int(mc_version)
        if version_int == 0:
            return 0
        
        if version_int >= 12005:  # 1.20.5+
            return 21
        elif version_int >= 11800:  # 1.18+
            return 17
        elif version_int >= 11700:  # 1.17
            return 16
        elif version_int >= 1200:  # 1.12+
            return 8
        else:
            return 8
    
    def search_java(self) -> list[JavaEntry]:
        """搜索系统中所有可用的 Java 安装。
        
        搜索位置（Windows）：
        1. JAVA_HOME 环境变量
        2. 常见安装路径（Program Files、Zulu、Adoptium 等）
        3. Windows 注册表
        4. .minecraft\runtime（官方启动器下载的 Java）
        5. PATH 环境变量
        
        Returns:
            Java 安装列表（已去重）
        """
        result: list[JavaEntry] = []
        seen_paths: set[str] = set()
        
        def add_java(path: str):
            """添加 Java 路径（去重）。"""
            if not path or path in seen_paths:
                return
            if os.path.isfile(path):
                seen_paths.add(path)
                result.append(JavaEntry(path=path))
        
        # 1. JAVA_HOME
        java_home = os.environ.get('JAVA_HOME')
        if java_home:
            javaw = os.path.join(java_home, 'bin', 'javaw.exe')
            java = os.path.join(java_home, 'bin', 'java.exe')
            add_java(javaw)
            add_java(java)
        
        # 2. 常见安装路径
        program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        
        common_bases = [
            os.path.join(program_files, 'Java'),
            os.path.join(program_files, 'Eclipse Adoptium'),
            os.path.join(program_files, 'Zulu'),
            os.path.join(program_files, 'BellSoft'),
            os.path.join(program_files, 'Microsoft'),
            os.path.join(program_files, 'Common Files', 'Oracle', 'Java'),
            os.path.join(program_files_x86, 'Java'),
        ]
        
        for base in common_bases:
            if not os.path.isdir(base):
                continue
            try:
                for subdir in os.listdir(base):
                    subdir_path = os.path.join(base, subdir)
                    if not os.path.isdir(subdir_path):
                        continue
                    javaw = os.path.join(subdir_path, 'bin', 'javaw.exe')
                    java = os.path.join(subdir_path, 'bin', 'java.exe')
                    add_java(javaw)
                    add_java(java)
            except (PermissionError, OSError):
                pass
        
        # 3. Windows 注册表
        if os.name == 'nt':
            result.extend(self._search_registry(seen_paths))
        
        # 4. .minecraft\runtime（官方启动器）
        appdata = os.environ.get('APPDATA', '')
        if appdata:
            minecraft_runtime = os.path.join(appdata, '.minecraft', 'runtime')
            if os.path.isdir(minecraft_runtime):
                try:
                    for root, dirs, files in os.walk(minecraft_runtime):
                        for file in files:
                            if file in ('javaw.exe', 'java.exe'):
                                add_java(os.path.join(root, file))
                except (PermissionError, OSError):
                    pass
        
        # 5. PATH 环境变量
        path_env = os.environ.get('PATH', '')
        for path_dir in path_env.split(os.pathsep):
            if not path_dir.strip():
                continue
            javaw = os.path.join(path_dir, 'javaw.exe')
            java = os.path.join(path_dir, 'java.exe')
            add_java(javaw)
            add_java(java)
        
        logger.info(f"Java 搜索完成，找到 {len(result)} 个候选")
        return result
    
    def _search_registry(self, seen_paths: set[str]) -> list[JavaEntry]:
        """从 Windows 注册表搜索 Java。"""
        result: list[JavaEntry] = []
        
        try:
            import winreg
        except ImportError:
            return result
        
        reg_keys = [
            r'SOFTWARE\JavaSoft\Java Runtime Environment',
            r'SOFTWARE\JavaSoft\Java Development Kit',
            r'SOFTWARE\JavaSoft\JDK',
            r'SOFTWARE\JavaSoft\JRE',
            r'SOFTWARE\Wow6432Node\JavaSoft\Java Runtime Environment',
            r'SOFTWARE\Wow6432Node\JavaSoft\Java Development Kit',
            r'SOFTWARE\Wow6432Node\JavaSoft\JDK',
            r'SOFTWARE\Wow6432Node\JavaSoft\JRE',
        ]
        
        for reg_key_path in reg_keys:
            try:
                reg_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_key_path)
            except FileNotFoundError:
                continue
            
            try:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(reg_key, i)
                        subkey = winreg.OpenKey(reg_key, subkey_name)
                        try:
                            java_home, _ = winreg.QueryValueEx(subkey, 'JavaHome')
                            javaw = os.path.join(java_home, 'bin', 'javaw.exe')
                            java = os.path.join(java_home, 'bin', 'java.exe')
                            
                            for path in [javaw, java]:
                                if path not in seen_paths and os.path.isfile(path):
                                    seen_paths.add(path)
                                    result.append(JavaEntry(path=path))
                        except FileNotFoundError:
                            pass
                        finally:
                            subkey.Close()
                        i += 1
                    except OSError:
                        break
            finally:
                reg_key.Close()
        
        return result
    
    def get_java_version(self, java_path: str) -> int:
        """获取 Java 的主版本号。
        
        执行 `java -version`，解析输出：
        - java version "1.8.0_291" -> 8
        - java version "17.0.1" -> 17
        
        Returns:
            Java 主版本号，0 表示失败
        """
        try:
            result = subprocess.run(
                [java_path, '-version'],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            # Java 输出在 stderr
            output = result.stderr if result.stderr else result.stdout
            
            # 匹配 version "x.y.z" 或 version "1.x.y"
            match = re.search(r'version\s+"(.+?)"', output)
            if not match:
                return 0
            
            version_str = match.group(1)
            
            # 处理 1.8.0 格式（Java 8 及以下）
            if version_str.startswith('1.'):
                parts = version_str.split('.')
                if len(parts) >= 2:
                    return int(parts[1])
            else:
                # 处理 17.0.1 格式（Java 9+）
                parts = version_str.split('.')
                if len(parts) >= 1:
                    return int(parts[0])
        
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, FileNotFoundError):
            pass
        
        return 0
    
    def find_suitable_java(self, mc_version: str, manual_path: str = "") -> Optional[str]:
        """为指定 MC 版本查找合适的 Java。
        
        策略：
        1. 如果提供了 manual_path 且有效，直接返回
        2. 确定 MC 版本所需的 Java 版本
        3. 搜索所有 Java 并获取版本信息
        4. 筛选满足要求的 Java（版本 >= 要求）
        5. 返回满足要求的最低版本（避免使用过高版本）
        
        Args:
            mc_version: Minecraft 版本号，如 "1.20.1"
            manual_path: 手动指定的 Java 路径（可选）
        
        Returns:
            Java 路径，None 表示未找到
        
        Raises:
            ValueError: MC 版本无法确定 Java 要求，或未找到合适的 Java
        """
        # 1. 手动路径优先
        if manual_path and os.path.isfile(manual_path):
            version = self.get_java_version(manual_path)
            required = self.get_java_requirement(mc_version)
            if version >= required:
                logger.info(f"使用手动指定的 Java {version}：{manual_path}")
                return manual_path
            else:
                raise ValueError(
                    f"手动指定的 Java {version} 不满足 MC {mc_version} 的要求（需要 Java {required}+）"
                )
        
        # 2. 确定要求
        required_version = self.get_java_requirement(mc_version)
        if required_version == 0:
            raise ValueError(f"无法确定 MC 版本 {mc_version} 所需的 Java 版本")
        
        # 3. 搜索并获取版本
        if not self._cache:
            self._cache = self.search_java()
        
        candidates: list[JavaEntry] = []
        for entry in self._cache:
            version = self.get_java_version(entry.path)
            if version > 0:
                entry.version = version
                candidates.append(entry)
        
        # 4. 筛选满足要求的（版本 >= 要求）
        suitable = [c for c in candidates if c.version >= required_version]
        
        # 5. 返回最低版本
        if suitable:
            suitable.sort(key=lambda x: x.version)
            selected = suitable[0]
            logger.info(f"自动选择 Java {selected.version}（MC {mc_version} 需要 {required_version}+）：{selected.path}")
            return selected.path
        
        raise ValueError(
            f"未找到适用的 Java {required_version}+（MC {mc_version}）。\n"
            f"找到 {len(candidates)} 个 Java：{[f'Java {c.version}' for c in candidates]}\n"
            f"请手动安装 Java {required_version} 或更高版本。"
        )
    
    def get_all_java(self) -> list[tuple[str, int]]:
        """获取所有检测到的 Java 列表。
        
        Returns:
            [(路径, 版本号), ...] 列表，按版本排序
        """
        if not self._cache:
            self._cache = self.search_java()
        
        result = []
        for entry in self._cache:
            if entry.version == 0:
                entry.version = self.get_java_version(entry.path)
            if entry.version > 0:
                result.append((entry.path, entry.version))
        
        result.sort(key=lambda x: x[1])
        return result
