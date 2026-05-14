"""自然语言日志检索工具 - 用日常语言查询 CLS 日志，无需编写 CQL 语句

该工具封装了 TextToSearchLogQuery → SearchLog 的两步调用链路，
用户只需用自然语言描述想查什么，工具自动完成查询语句生成和日志检索。
"""

from typing import Optional

from langchain_core.tools import tool
from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry


@tool
async def query_log(
    question: str,
    topic_id: Optional[str] = None,
    hours_ago: int = 1,
) -> str:
    """用自然语言查询腾讯云 CLS 日志，无需手动编写 CQL 查询语句

    当用户说"查一下最近15分钟的错误日志"或"服务下线前有哪些异常日志"
    时，直接调用此工具，它会自动将自然语言转为 CQL 并执行检索。

    Args:
        question: 自然语言描述的查询需求（必填）
            示例: "最近15分钟的错误日志"
                  "服务下线前的异常日志"
                  "包含timeout的日志"
        topic_id: 日志主题ID（可选，不传则先通过 GetTopicInfoByName 查找）
        hours_ago: 查询最近多少小时的日志，默认 1 小时

    Returns:
        str: 格式化后的日志查询结果
    """
    try:
        # 获取 MCP 客户端和 CLS 工具
        mcp_client = await get_mcp_client_with_retry()
        all_tools = await mcp_client.get_tools()

        # 按名称索引 MCP 工具
        tools_by_name = {}
        for t in all_tools:
            tools_by_name[t.name] = t

        # 必需工具检查
        missing = []
        if "TextToSearchLogQuery" not in tools_by_name:
            missing.append("TextToSearchLogQuery")
        if "SearchLog" not in tools_by_name:
            missing.append("SearchLog")
        if missing:
            return f"CLS MCP 服务缺少必需工具: {', '.join(missing)}，请检查 MCP 服务是否正常。\n当前可用工具: {list(tools_by_name.keys())}"

        text_to_cql = tools_by_name["TextToSearchLogQuery"]
        search_log = tools_by_name["SearchLog"]

        # 如果没指定 topic_id，先获取当前时间辅助后续查询
        from app.tools.time_tool import get_current_time
        current_time = get_current_time.invoke({})
        logger.info(f"query_log: question='{question}', topic_id={topic_id}, hours_ago={hours_ago}")

        # 步骤1: 自然语言 → CQL 查询语句
        logger.info("Step 1: 调用 TextToSearchLogQuery 将自然语言转为 CQL")
        cql_result = await text_to_cql.ainvoke({"Text": question})
        cql_text = _extract_text(cql_result, "query")
        logger.info(f"生成的 CQL: {cql_text[:200] if cql_text else '空'}")

        if not cql_text:
            return f"无法将问题转换为日志查询语句: '{question}'。请尝试更具体的描述。"

        # 步骤2: 执行 CQL 查询
        logger.info("Step 2: 调用 SearchLog 执行 CQL 查询")

        search_params = {
            "Query": cql_text,
            "Limit": 20,
        }

        if topic_id:
            search_params["TopicId"] = topic_id

        # 如果有 From/To 时间戳参数，传入 SearchLog；不传则由 MCP 使用默认范围
        search_result = await search_log.ainvoke(search_params)
        result_text = _extract_text(search_result, "logs")

        if not result_text:
            # SearchLog 可能直接返回了 JSON 字符串
            raw = search_result
            if isinstance(raw, str):
                result_text = raw
            else:
                result_text = str(raw)

        logger.info(f"SearchLog 返回结果长度: {len(result_text)}")
        return result_text

    except Exception as e:
        logger.error(f"query_log 查询失败: {e}")
        return (
            f"日志查询失败: {str(e)}\n"
            f"请检查:\n"
            f"1. CLS MCP 服务是否正常连接\n"
            f"2. 自然语言描述是否清晰（如包含时间范围、错误级别等）\n"
            f"3. 日志主题是否存在且有权访问"
        )


def _extract_text(result, default_key: str = "query") -> str:
    """从 MCP 工具返回结果中提取文本内容

    兼容多种返回格式：
    - 直接字符串
    - [{"text": "..."}] 格式 (MCP CallToolResult)
    - {"query": "..."} 字典格式
    - 其他格式降级为 str()

    Args:
        result: MCP 工具返回的原始结果
        default_key: 字典格式时的默认提取键名

    Returns:
        str: 提取的文本内容
    """
    # 字符串直接返回
    if isinstance(result, str):
        return result

    # [{"text": "..."}] 格式
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts) if parts else str(result)

    # {"query": "..."} 或类似字典格式
    if isinstance(result, dict):
        if default_key in result:
            value = result[default_key]
            return value if isinstance(value, str) else str(value)
        return str(result)

    return str(result)
