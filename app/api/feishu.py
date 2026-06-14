"""飞书消息接口 — 通过飞书 MCP 发送群消息"""

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

router = APIRouter()

FEISHU_MCP_URL = "http://127.0.0.1:8007/mcp"
FEISHU_CHAT_ID = "oc_1e24a302aea8da5ac47588437c238260"  # chase 群


class FeishuTextRequest(BaseModel):
    text: str = Field(..., description="消息文本内容", max_length=15000)


class FeishuCardRequest(BaseModel):
    title: str = Field(..., description="卡片标题")
    content: str = Field(..., description="卡片内容 (支持 Markdown)")
    level: str = Field(default="warning", description="告警级别: info/warning/error/critical")


async def _call_feishu_mcp(tool_name: str, arguments: dict) -> dict:
    """调用飞书 MCP 工具。"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            FEISHU_MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        resp.raise_for_status()
        body = resp.text
        # 提取 SSE data 中的 JSON
        if body.startswith("event: message\n"):
            body = body.split("data: ", 1)[1]
        return httpx.Response(200, content=body).json()  # pyright: ignore[reportAny]


@router.post("/feishu/send")
async def send_text(request: FeishuTextRequest):
    """发送文本消息到飞书群。

    POST /api/feishu/send
    {"text": "消息内容"}
    """
    import json as _json

    try:
        result = await _call_feishu_mcp(
            "im_v1_message_create",
            {
                "params": {"receive_id_type": "chat_id"},
                "data": {
                    "receive_id": FEISHU_CHAT_ID,
                    "msg_type": "text",
                    "content": _json.dumps({"text": request.text}),
                },
            },
        )
        logger.info(f"飞书消息发送成功: {request.text[:50]}...")
        return {"code": 200, "message": "发送成功", "data": result}
    except Exception as e:
        logger.error(f"飞书消息发送失败: {e}")
        raise HTTPException(status_code=500, detail=f"发送失败: {e}")


@router.post("/feishu/send_card")
async def send_card(request: FeishuCardRequest):
    """发送卡片消息到飞书群。

    POST /api/feishu/send_card
    {"title": "告警标题", "content": "告警内容", "level": "warning"}
    """
    import json as _json

    colors = {
        "info": "blue",
        "warning": "yellow",
        "error": "red",
        "critical": "purple",
    }
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": request.title},
            "template": colors.get(request.level, "yellow"),
        },
        "elements": [
            {"tag": "markdown", "content": request.content},
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "OnCall AIOps · 自动通知",
                    }
                ],
            },
        ],
    }
    try:
        result = await _call_feishu_mcp(
            "im_v1_message_create",
            {
                "params": {"receive_id_type": "chat_id"},
                "data": {
                    "receive_id": FEISHU_CHAT_ID,
                    "msg_type": "interactive",
                    "content": _json.dumps(card),
                },
            },
        )
        logger.info(f"飞书卡片发送成功: {request.title}")
        return {"code": 200, "message": "卡片发送成功", "data": result}
    except Exception as e:
        logger.error(f"飞书卡片发送失败: {e}")
        raise HTTPException(status_code=500, detail=f"发送失败: {e}")
