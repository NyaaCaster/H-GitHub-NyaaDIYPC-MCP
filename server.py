"""
NyaaDIYPC-MCP 服务入口。

Python MCP SDK Streamable HTTP 传输。`streamable_http_app()` 返回的
Starlette app 默认以 /mcp 为端点，无需外层 Mount。顶层 ASGI wrapper
负责 Bearer token 鉴权（对标 NyaaQiny-MCP）和 /health 公开端点。

启动:
    python server.py
    uvicorn server:app --host 0.0.0.0 --port 5115
"""

import os
import json

from app.db.schema import init_db
from app.mcp.server import create_mcp_server
from app.mcp.auth import BearerAuthWrapper


def build_app():
    """组装 ASGI app：MCP streamable HTTP + Bearer 鉴权 + /health。"""

    # 启动时自动初始化数据库（解决 P1 遗留项）
    db_path = os.getenv("DIYPC_DB_PATH", "/app/data/diypc.db")
    db_parent = os.path.dirname(db_path)
    if db_parent and not os.path.exists(db_parent):
        os.makedirs(db_parent, exist_ok=True)
    init_db(db_path)

    mcp = create_mcp_server()

    # MCP SDK 产出 Starlette ASGI app，内部已挂载 /mcp 路径处理
    mcp_starlette = mcp.streamable_http_app()

    # 鉴权（对标 NyaaQiny-MCP 多 token Bearer 模式）
    auth = BearerAuthWrapper.from_env()

    # 顶层 ASGI：路径分发
    async def app(scope, receive, send):
        if scope["type"] != "http":
            await mcp_starlette(scope, receive, send)
            return

        path = scope.get("path", "")

        # /health 始终公开
        if path == "/health":
            body = json.dumps({
                "status": "ok",
                "service": "nyaadiypc-mcp",
                "version": "0.1.0",
            }).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        # /mcp 需要 Bearer token（若配置了 token）
        if path.startswith("/mcp"):
            valid_tokens = auth.valid_tokens
            if valid_tokens:
                headers = dict(scope.get("headers", []))
                auth_bytes = headers.get(b"authorization", b"")
                auth_str = auth_bytes.decode("latin-1")
                token = auth_str[7:].strip() if auth_str.startswith("Bearer ") else None
                if not token or token not in valid_tokens:
                    body = b'{"error":"Unauthorized","detail":"Invalid or missing Bearer token"}'
                    await send({
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({"type": "http.response.body", "body": body})
                    return

        # 交给 MCP app 处理
        await mcp_starlette(scope, receive, send)

    return app


# 给 uvicorn 使用的模块级 app
app = build_app()


# ---- 直接运行 ----
if __name__ == "__main__":
    import uvicorn
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "5115"))
    uvicorn.run(app, host=host, port=port)
