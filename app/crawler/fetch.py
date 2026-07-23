"""
异步 HTTP 会话管理 — Referer / UA / 重试 / 限速 / GBK 解码。

Usage:
    async with CrawlSession() as session:
        text = await session.fetch("https://...", referer="...")
        data = await session.fetch_json("https://...", referer="...")
"""

import asyncio
import json
import logging
import os
import random
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---- 默认配置（可由环境变量覆盖） ----
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

RETRY_BACKOFFS = (1.0, 3.0, 5.0)  # 三次重试退避秒数


class CrawlSession:
    """异步 HTTP 会话，封装反爬礼貌与 GBK 解码。

    - 并发上限由 CRAWL_CONCURRENCY env（默认 2）控制
    - 请求间隔 ≥ CRAWL_MIN_INTERVAL_S（默认 1.0s），加 0~1.5s 随机抖动
    - 自动 Referer / UA / 超时
    - GBK 响应自动 decode 为 UTF-8 字符串
    """

    def __init__(self) -> None:
        self._concurrency = int(os.getenv("CRAWL_CONCURRENCY", "2"))
        self._min_interval = float(os.getenv("CRAWL_MIN_INTERVAL_S", "1.0"))
        self._sem: Optional[asyncio.Semaphore] = None
        self._last_request: float = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "CrawlSession":
        self._sem = asyncio.Semaphore(self._concurrency)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=60.0),
            limits=httpx.Limits(max_connections=self._concurrency + 2),
            headers={"User-Agent": DEFAULT_UA},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _rate_limit(self) -> None:
        """确保并发 ≤ CRAWL_CONCURRENCY，且请求间隔 ≥ min_interval + 随机抖动。"""
        assert self._sem is not None
        await self._sem.acquire()
        try:
            now = time.monotonic()
            since_last = now - self._last_request
            min_wait = self._min_interval + random.uniform(0, 1.5)
            if since_last < min_wait:
                await asyncio.sleep(min_wait - since_last)
            self._last_request = time.monotonic()
        finally:
            self._sem.release()

    async def fetch(
        self,
        url: str,
        referer: str = "https://zj.zol.com.cn/",
    ) -> str:
        """GET 请求 → GBK 解码 → UTF-8 文本。含 3 次退避重试。

        Args:
            url: 目标 URL。
            referer: Referer 头（ZOL 必需，否则跳 checking 页）。

        Returns:
            解码后的 UTF-8 文本。

        Raises:
            httpx.HTTPError: 3 次重试后仍失败。
        """
        assert self._client is not None, "CrawlSession not entered"

        headers = {
            "Referer": referer,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        last_exc: Optional[Exception] = None
        for attempt, backoff in enumerate(RETRY_BACKOFFS, start=1):
            try:
                await self._rate_limit()
                resp = await self._client.get(url, headers=headers)
                resp.raise_for_status()
                # ZOL 全部页面 charset=GBK
                raw = resp.content
                try:
                    return raw.decode("gbk")
                except UnicodeDecodeError:
                    # 极少数页面可能已经是 UTF-8
                    return raw.decode("utf-8")
            except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                logger.warning(
                    "[%d/%d] %s → %s: %s, retry in %.1fs",
                    attempt,
                    len(RETRY_BACKOFFS),
                    url[:80],
                    type(exc).__name__,
                    exc,
                    backoff,
                )
                if attempt < len(RETRY_BACKOFFS):
                    await asyncio.sleep(backoff)

        raise last_exc  # type: ignore[misc]

    async def fetch_json(
        self,
        url: str,
        referer: str = "https://zj.zol.com.cn/",
    ) -> dict:
        """GET → JSON dict。内部调用 fetch() 获取文本后 json.loads。"""
        text = await self.fetch(url, referer=referer)
        return json.loads(text)
