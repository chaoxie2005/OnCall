"""Prometheus 告警查询工具 - 实时获取 Prometheus 告警信息，为故障诊断提供第一手数据"""

from typing import Any

import httpx
from langchain_core.tools import tool
from loguru import logger

from app.config import config


def _extract_alerts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Prometheus API 响应中提取告警关键字段

    解析 /api/v1/alerts 返回的 JSON，提取 alertname、description、activeAt 等字段，
    只保留告警级别和触发时间匹配的活跃告警。

    Args:
        data: Prometheus API 响应的完整 JSON

    Returns:
        结构化告警列表，每项包含 alertname, instance, severity,
        description, status, activeAt
    """
    raw = data.get("data", {}).get("alerts", [])
    if not raw:
        return []

    filtered: list[dict[str, Any]] = []

    for alert in raw:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        state = alert.get("state", "")

        entry = {
            "alertname": labels.get("alertname", "未知告警"),
            "instance": labels.get("instance", "未知实例"),
            "severity": labels.get("severity", "未知"),
            "description": annotations.get("description", alert.get("annotations", {}).get("summary", "无详情")),
            "status": state,
            "activeAt": alert.get("activeAt", "未知"),
        }

        # 过滤规则：只保留 firing 状态的告警，未传入或空列表时保留全部
        if config.prometheus_alert_states:
            if state in config.prometheus_alert_states:
                filtered.append(entry)
        else:
            filtered.append(entry)

    return filtered


@tool
def query_prometheus_alerts() -> str:
    """查询 Prometheus 告警信息，实时获取当前活跃的故障告警

    调用 Prometheus AlertManager API (GET /api/v1/alerts)，提取告警名称、
    详情、触发时间等关键字段。当用户需要了解线上故障现状、排查服务异常时，
    优先使用此工具获取故障诊断的起点数据。

    Returns:
        str: 格式化的告警信息文本，无告警时返回相应提示
    """
    url = f"{config.prometheus_base_url}/api/v1/alerts"

    try:
        logger.info(f"查询 Prometheus 告警: {url}")

        resp = httpx.get(url, timeout=config.prometheus_timeout)

        if resp.status_code != 200:
            logger.error(
                f"Prometheus API 返回非 200: status={resp.status_code}, "
                f"body={resp.text[:500]}"
            )
            return (
                f"调用 Prometheus API 失败，HTTP {resp.status_code}。"
                f"请检查 Prometheus 服务是否正常运行。"
            )

        data = resp.json()
        alerts = _extract_alerts(data)

        if not alerts:
            return "当前没有活跃的 Prometheus 告警。"

        lines = [f"共 {len(alerts)} 条活跃告警：\n"]
        for i, a in enumerate(alerts, 1):
            lines.append(
                f"【告警 {i}】\n"
                f"  告警名称: {a['alertname']}\n"
                f"  告警级别: {a['severity']}\n"
                f"  详情描述: {a['description']}\n"
                f"  影响实例: {a['instance']}\n"
                f"  触发时间: {a['activeAt']}\n"
                f"  状态: {a['status']}\n"
            )

        return "\n".join(lines)

    except httpx.TimeoutException:
        logger.error(f"Prometheus API 请求超时: {url}")
        return "查询 Prometheus 告警超时，请稍后重试。"
    except httpx.ConnectError:
        logger.error(f"无法连接 Prometheus 服务: {url}")
        return "无法连接 Prometheus 服务，请检查网络和 Prometheus 地址配置。"
    except Exception as e:
        logger.error(f"查询 Prometheus 告警失败: {e}")
        return f"查询 Prometheus 告警时发生错误: {str(e)}"
