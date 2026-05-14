"""速率限制模块

基于 slowapi + limits 提供 IP 级别的请求速率限制。
默认使用内存存储，可配置切换到 Redis。

响应头（可通过 Response Headers 查看配额状态）:
    X-RateLimit-Limit       — 时间窗口内允许的最大请求数
    X-RateLimit-Remaining   — 窗口内剩余可请求次数
    X-RateLimit-Reset       — 窗口重置时间（Unix timestamp）
    Retry-After             — 触发限流后，建议重试等待秒数
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import config

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=config.rate_limit_storage_uri,
    headers_enabled=config.rate_limit_headers_enabled,
    default_limits=[],
)


def conditional_limit(limit_value: str):
    """条件速率限制装饰器 — 仅当 rate_limit_enabled=True 时生效。

    Usage:
        @router.post("/chat")
        @conditional_limit(config.rate_limit_chat)
        async def chat(...): ...

    当限流关闭时，装饰器为透传 no-op，零开销。
    """
    if not config.rate_limit_enabled:
        return lambda func: func
    return limiter.limit(limit_value)
