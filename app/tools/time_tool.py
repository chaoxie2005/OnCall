"""时间工具 - 获取当前时间信息，解决大模型时间失忆问题"""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from loguru import logger


@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前时间，返回多格式时间信息。

    当工具需要时间参数时（如查询过去1小时的告警），Agent应自动前置调用此工具。
    也可直接响应用户的时间询问（"现在几点"、"今天日期"等）。

    Args:
        timezone: 时区，默认为 Asia/Shanghai（北京时间）

    Returns:
        str: 包含秒级时间戳、毫秒级时间戳、格式化日期时间的文本
    """
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)

        timestamp_s = int(now.timestamp())
        timestamp_ms = int(now.timestamp() * 1000)
        formatted = now.strftime('%Y-%m-%d %H:%M:%S')

        return (
            f"当前时间（{timezone}）：\n"
            f"  - 日期时间: {formatted}\n"
            f"  - 秒级时间戳: {timestamp_s}\n"
            f"  - 毫秒级时间戳: {timestamp_ms}"
        )

    except Exception as e:
        logger.error(f"时间查询工具调用失败: {e}")
        return f"获取时间失败: {str(e)}"
