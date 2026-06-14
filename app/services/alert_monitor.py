"""告警监控服务 — 后台轮询 Prometheus + Monitor MCP，检测到告警自动推送飞书

覆盖: CPU / 内存 / 磁盘 超阈值 + Prometheus 不可达 + Prometheus 自身告警
"""

import asyncio
import json as _json
import time
from typing import Optional

import httpx
from loguru import logger

from app.config import config

# ── 状态存储 ──
FEISHU_CHAT_ID = "oc_1e24a302aea8da5ac47588437c238260"
FEISHU_MCP_URL = "http://127.0.0.1:8007/mcp"
MONITOR_MCP_URL = "http://127.0.0.1:8004/mcp"

_alert_state = {
    "enabled": True,
    "current_alerts": [],
    "fired_alerts": set(),
    "resolved_alerts": set(),
    "history": [],
    "last_check": None,
    "check_interval": 60,
}

ALERT_COLORS = {
    "critical": "purple",
    "warning": "yellow",
    "error": "red",
    "info": "blue",
}

# Monitor MCP 需要检查的指标列表
MONITOR_CHECKS = [
    {"tool": "query_cpu_metrics",    "alert_name": "CPUHighUsage",    "threshold_key": "cpu",    "severity": "error"},
    {"tool": "query_memory_metrics", "alert_name": "MemoryHighUsage", "threshold_key": "memory", "severity": "warning"},
    {"tool": "query_disk_metrics",   "alert_name": "DiskHighUsage",   "threshold_key": "disk",   "severity": "critical"},
]

PROMETHEUS_ADDR = config.prometheus_base_url


def get_alert_status() -> dict:
    return {
        "enabled": _alert_state["enabled"],
        "current_alerts": _alert_state["current_alerts"],
        "history": _alert_state["history"][-20:],
        "last_check": _alert_state["last_check"],
        "check_interval": _alert_state["check_interval"],
    }


def toggle_alert(enabled: Optional[bool] = None) -> dict:
    if enabled is not None:
        _alert_state["enabled"] = enabled
        logger.info(f"告警监控已{'启用' if enabled else '暂停'}")
    return {"enabled": _alert_state["enabled"]}


def _alert_key(alert: dict) -> str:
    return f"{alert.get('alertname','')}:{alert.get('instance','')}"


async def _send_feishu_card(title: str, content: str, level: str) -> bool:
    """通过飞书 MCP 发送卡片消息（群成员手机上会收到飞书通知）。"""
    color = ALERT_COLORS.get(level, "yellow")
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [
            {"tag": "markdown", "content": content},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "OnCall 自动告警监控"}]},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                FEISHU_MCP_URL,
                json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "im_v1_message_create",
                        "arguments": {
                            "params": {"receive_id_type": "chat_id"},
                            "data": {
                                "receive_id": FEISHU_CHAT_ID,
                                "msg_type": "interactive",
                                "content": _json.dumps(card),
                            },
                        },
                    },
                },
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            )
            resp.raise_for_status()
            logger.info(f"飞书告警卡片已发送: {title}")
            return True
    except Exception as e:
        logger.error(f"飞书告警推送失败: {e}")
        return False


async def _init_monitor_session(client: httpx.AsyncClient) -> str:
    """初始化 Monitor MCP 会话，返回 session_id。"""
    init_resp = await client.post(
        MONITOR_MCP_URL,
        json={
            "jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "alert-monitor", "version": "1.0"},
            },
        },
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    session_id = init_resp.headers.get("mcp-session-id", "")
    if session_id:
        await client.post(
            MONITOR_MCP_URL,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "mcp-session-id": session_id,
            },
        )
    return session_id


async def _call_monitor_tool(client: httpx.AsyncClient, session_id: str, tool_name: str) -> Optional[dict]:
    """调用 Monitor MCP 工具并返回解析后的结果。"""
    try:
        resp = await client.post(
            MONITOR_MCP_URL,
            json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool_name, "arguments": {"service_name": "data-sync-service"}},
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "mcp-session-id": session_id,
            },
        )
        if resp.status_code != 200:
            return None
        body = resp.text
        if "data: " in body:
            body = body.split("data: ", 1)[1].split("\n")[0]
        data = _json.loads(body)
        content = data.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            return _json.loads(content[0]["text"])
    except Exception:
        pass
    return None


async def _check_prometheus_alerts() -> tuple[list[dict], Optional[str]]:
    """查询 Prometheus 活跃告警。返回 (告警列表, 错误信息)。"""
    try:
        async with httpx.AsyncClient(timeout=config.prometheus_timeout) as client:
            resp = await client.get(f"{PROMETHEUS_ADDR}/api/v1/alerts")
            if resp.status_code != 200:
                msg = f"Prometheus API 返回 HTTP {resp.status_code}"
                logger.warning(msg)
                return [], msg
            data = resp.json()
            raw = data.get("data", {}).get("alerts", [])
            alerts = []
            for a in raw:
                labels = a.get("labels", {})
                annotations = a.get("annotations", {})
                state = a.get("state", "")
                if state != "firing":
                    continue
                if config.prometheus_alert_states and state not in config.prometheus_alert_states:
                    continue
                alerts.append({
                    "alertname": labels.get("alertname", "未知告警"),
                    "instance": labels.get("instance", "未知实例"),
                    "severity": labels.get("severity", "unknown"),
                    "description": annotations.get("description", annotations.get("summary", "")),
                    "status": state,
                    "activeAt": a.get("activeAt", ""),
                    "source": "prometheus",
                })
            return alerts, None
    except httpx.ConnectError:
        msg = f"无法连接 Prometheus ({PROMETHEUS_ADDR})"
        logger.warning(msg)
        return [], msg
    except httpx.TimeoutException:
        msg = f"访问 Prometheus ({PROMETHEUS_ADDR}) 超时"
        logger.warning(msg)
        return [], msg
    except Exception as e:
        msg = f"查询 Prometheus 异常: {e}"
        logger.warning(msg)
        return [], msg


async def _check_monitor_metrics() -> list[dict]:
    """通过 Monitor MCP 检查 CPU/内存/磁盘指标。"""
    alerts = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            session_id = await _init_monitor_session(client)
            if not session_id:
                return []

            for check in MONITOR_CHECKS:
                result = await _call_monitor_tool(client, session_id, check["tool"])
                if result is None:
                    continue
                alert_info = result.get("alert_info", {})
                stats = result.get("statistics", {})
                if alert_info.get("triggered"):
                    host_ip = result.get("host_ip", "unknown")
                    hostname = result.get("hostname", "unknown")
                    threshold = alert_info.get("threshold", "?")
                    description = (
                        f"平均: {stats.get('avg', '?')}% | "
                        f"峰值: {stats.get('max', '?')}% | "
                        f"P95: {stats.get('p95', '?')}% | "
                        f"阈值: {threshold}%"
                    )
                    alerts.append({
                        "alertname": check["alert_name"],
                        "instance": f"{hostname} ({host_ip}) — {result.get('service_name', 'data-sync-service')}",
                        "host_ip": host_ip,
                        "hostname": hostname,
                        "severity": check["severity"],
                        "description": description,
                        "suggestions": alert_info.get("suggestions", ""),
                        "status": "firing",
                        "activeAt": "",
                        "source": "monitor",
                    })
    except Exception as e:
        logger.debug(f"Monitor MCP 指标查询失败: {e}")
    return alerts


async def alert_monitor_loop():
    """后台告警监控主循环。"""
    logger.info("告警监控服务已启动，间隔 {} 秒", _alert_state["check_interval"])
    logger.info("监控范围: CPU/内存/磁盘 + Prometheus 可达性 + Prometheus 自身告警")
    logger.info("飞书目标群: {}", FEISHU_CHAT_ID)
    await asyncio.sleep(10)

    while True:
        try:
            await _alert_check_cycle()
        except Exception as e:
            logger.error(f"告警检查异常: {e}")
        await asyncio.sleep(_alert_state["check_interval"])


async def _alert_check_cycle():
    """单次告警检查周期。"""
    if not _alert_state["enabled"]:
        return

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    _alert_state["last_check"] = now

    all_alerts: list[dict] = []

    # 1. Prometheus 自身告警
    prom_alerts, prom_error = await _check_prometheus_alerts()
    all_alerts.extend(prom_alerts)

    # 2. Prometheus 不可达告警
    if prom_error:
        all_alerts.append({
            "alertname": "PrometheusUnreachable",
            "instance": PROMETHEUS_ADDR,
            "host_ip": PROMETHEUS_ADDR,
            "hostname": "Prometheus",
            "severity": "error",
            "description": prom_error,
            "suggestions": (
                "1. 检查 Prometheus 服务是否运行（`systemctl status prometheus`）\n"
                "2. 确认 Prometheus 端口是否可达（`telnet <host> 9090`）\n"
                "3. 查看 Prometheus 日志排查启动异常\n"
                "4. 检查防火墙/安全组是否放通 9090 端口"
            ),
            "status": "firing",
            "activeAt": now,
            "source": "system",
        })

    # 3. Monitor MCP 指标告警（CPU / 内存 / 磁盘）
    monitor_alerts = await _check_monitor_metrics()
    all_alerts.extend(monitor_alerts)

    current_keys = {_alert_key(a) for a in all_alerts}
    _alert_state["current_alerts"] = all_alerts

    # ── 新告警 → 推送飞书 ──
    new_keys = current_keys - _alert_state["fired_alerts"]
    for alert in all_alerts:
        key = _alert_key(alert)
        if key in new_keys:
            severity = alert.get("severity", "warning")
            emoji = "🟣" if severity == "critical" else "🔴" if severity == "error" else "⚠️"
            title = f"{emoji} {alert['alertname']}"

            # 构建详细卡片内容
            host_ip = alert.get("host_ip", alert.get("instance", "unknown"))
            hostname = alert.get("hostname", "")
            host_line = f"**主机:** {hostname} (`{host_ip}`)" if hostname else f"**主机:** `{host_ip}`"

            content = (
                f"{host_line}\n"
                f"**级别:** {severity}\n"
                f"**指标:** {alert.get('description', '无')}\n"
                f"**来源:** {alert.get('source', 'unknown')}\n"
                f"**时间:** {alert.get('activeAt', now)}"
            )

            # 附加修复建议
            suggestions = alert.get("suggestions", "")
            if suggestions:
                content += f"\n\n---\n**💡 处理建议:**\n{suggestions}"

            success = await _send_feishu_card(title, content, severity)
            _alert_state["fired_alerts"].add(key)
            _alert_state["history"].append({
                "alertname": alert["alertname"],
                "instance": alert["instance"],
                "severity": severity,
                "action": "firing",
                "time": now,
                "pushed": success,
            })

    # ── 已恢复告警 → 推送飞书 ──
    resolved_keys = _alert_state["fired_alerts"] - current_keys
    for key in resolved_keys.copy():
        if key not in _alert_state["resolved_alerts"]:
            alertname = key.split(":", 1)[0]
            instance = key.split(":", 1)[1] if ":" in key else "unknown"
            title = f"✅ {alertname} 已恢复"
            content = f"**实例:** {instance}  \n**恢复时间:** {now}"
            await _send_feishu_card(title, content, "info")
            _alert_state["resolved_alerts"].add(key)
            _alert_state["fired_alerts"].discard(key)
            _alert_state["history"].append({
                "alertname": alertname,
                "instance": instance,
                "severity": "info",
                "action": "resolved",
                "time": now,
                "pushed": True,
            })

    # 清理恢复记录
    _alert_state["resolved_alerts"] &= _alert_state["fired_alerts"]

    if len(_alert_state["history"]) > 50:
        _alert_state["history"] = _alert_state["history"][-50:]

    if new_keys:
        logger.info(f"检测到 {len(new_keys)} 条新告警，已推送飞书")
    if resolved_keys - _alert_state["resolved_alerts"]:
        logger.info(f"检测到 {len(resolved_keys - _alert_state['resolved_alerts'])} 条告警恢复")
