"""
Bearer token authentication — 对标 NyaaQiny-MCP 多 token 鉴权模式。

从 MCP_API_KEY 和 MCP_API_KEY_* 环境变量收集所有有效 token，
启动时冻结，进程生命周期不变。

Usage:
    auth = BearerAuthWrapper.from_env()
    tokens = auth.valid_tokens   # frozenset[str]
"""

import os
from typing import AbstractSet


def _collect_valid_tokens() -> AbstractSet[str]:
    """Scan process env for MCP_API_KEY + MCP_API_KEY_<LABEL>."""
    tokens: set[str] = set()
    for key, val in os.environ.items():
        if key.startswith("MCP_API_KEY") and val:
            tokens.add(val)
    return frozenset(tokens)


class BearerAuthWrapper:
    """Holds a validated token set frozen at construction time.

    Token validation is performed inline in server.py's ASGI wrapper,
    not here — this class is purely a token holder.
    """

    def __init__(self, tokens: AbstractSet[str]):
        self._tokens = tokens

    @classmethod
    def from_env(cls) -> "BearerAuthWrapper":
        return cls(_collect_valid_tokens())

    @property
    def valid_tokens(self) -> AbstractSet[str]:
        return self._tokens
