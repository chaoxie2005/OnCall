"""告警控制接口 — 告警状态查询、开关控制、历史记录"""

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.alert_monitor import get_alert_status, toggle_alert

router = APIRouter()


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/alerts/status")
async def alerts_status():
    """获取当前告警监控状态。

    GET /api/alerts/status
    """
    return {"code": 200, "message": "success", "data": get_alert_status()}


@router.post("/alerts/toggle")
async def alerts_toggle(request: ToggleRequest):
    """启用/暂停告警监控。

    POST /api/alerts/toggle
    {"enabled": true}
    """
    result = toggle_alert(request.enabled)
    return {
        "code": 200,
        "message": f"告警监控已{'启用' if result['enabled'] else '暂停'}",
        "data": result,
    }
