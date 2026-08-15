"""
隧道代理后端（第三方代理服务 API）

从第三方代理服务商 API 获取代理，支持:
  - 隧道代理（固定域名 + 动态出口 IP）
  - API 提取（每次获取新 IP）
  - 自动换 IP（通过 API 调用）

配置来源: jimmyspider 全局配置（jimmyspider/config.py）
  - PROXY_TUNNEL_URL: 隧道代理完整地址，格式 http://user:pass@host:port
  - PROXY_API_URL:    API 提取完整地址（含鉴权参数）

参考: jimmyspider/proxy.py ProxyUtil.get_proxy_tunel(), _fetch_new_proxy()
"""

import time
import asyncio
from typing import Optional
from urllib.parse import urlparse, unquote

import aiohttp

from ..base import ProxyBackend, ProxyInfo
from jimmyspider.config import get_config


class TunnelAPIBackend(ProxyBackend):
    """
    隧道代理后端

    支持两种模式:
      1. tunnel: 固定隧道地址（PROXY_TUNNEL_URL 配置）- 每次请求自动换 IP
      2. api: API 提取模式（PROXY_API_URL 配置）- 调用 API 获取新 IP

    模式 1 (tunnel): ProxyInfo 始终返回同一个代理地址，
                    但服务商会在服务端轮换出口 IP
    模式 2 (api): 每次 get_proxy 调用 API 提取新 IP
    """

    backend_name = "tunnel_api"

    def __init__(self, tunnel_url: str = None, api_url: str = None,
                 mode: str = "tunnel"):  # tunnel / api
        cfg = get_config()
        # 隧道模式: 统一从 PROXY_TUNNEL_URL 解析 (http://user:pass@host:port)
        tunnel_url = tunnel_url or cfg.PROXY_TUNNEL_URL
        self.tunnel_url = tunnel_url
        if tunnel_url:
            parsed = urlparse(tunnel_url)
            self.tunnel_host = parsed.hostname or "127.0.0.1"
            self.tunnel_port = parsed.port or 80
            self.tunnel_user = unquote(parsed.username) if parsed.username else ""
            self.tunnel_pass = unquote(parsed.password) if parsed.password else ""
        else:
            self.tunnel_host = None
            self.tunnel_port = 0
            self.tunnel_user = ""
            self.tunnel_pass = ""

        # API 模式: PROXY_API_URL 为完整提取地址（含鉴权参数）
        self.api_url = api_url or cfg.PROXY_API_URL

        self.mode = mode
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_fetch: float = 0
        self._fetch_interval: float = 3.0  # API 最小间隔

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    # ---- 获取代理 ----
    async def get_proxy(self, tags: list[str] = None) -> Optional[ProxyInfo]:
        if self.mode == "tunnel":
            if not self.tunnel_url:
                # 未配置隧道代理
                return None
            return ProxyInfo(
                host=self.tunnel_host,
                port=self.tunnel_port,
                scheme="http",
                username=self.tunnel_user,
                password=self.tunnel_pass,
                source="tunnel_api",
                tags=["tunnel"],
            )
        else:
            return await self._fetch_from_api()

    async def _fetch_from_api(self) -> Optional[ProxyInfo]:
        """从 API 提取代理"""
        if not self.api_url:
            return None

        now = time.time()
        if now - self._last_fetch < self._fetch_interval:
            await asyncio.sleep(self._fetch_interval - (now - self._last_fetch))

        session = await self._get_session()
        try:
            async with session.get(
                self.api_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
                proxy_list = data.get("data", {}).get("proxy_list", [])
                if not proxy_list:
                    return None

                self._last_fetch = time.time()
                parts = proxy_list[0].split(":")
                if len(parts) == 2:
                    return ProxyInfo(
                        host=parts[0], port=int(parts[1]),
                        scheme="http",
                        source="tunnel_api",
                        tags=["api"],
                    )
        except Exception:
            pass
        return None

    # ---- 报告 ----
    async def report_success(self, proxy: ProxyInfo) -> None:
        proxy.record_success()

    async def report_failure(self, proxy: ProxyInfo, error: str = "") -> None:
        proxy.record_failure()

    # ---- 健康检查 ----
    async def health_check(self) -> dict:
        return {
            "backend": self.backend_name,
            "mode": self.mode,
            "configured": bool(self.tunnel_url or self.api_url),
            "tunnel": self.tunnel_url if self.mode == "tunnel" else "N/A",
            "last_fetch": self._last_fetch,
        }

    async def get_stats(self) -> dict:
        return await self.health_check()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
