"""高德地图 MCP Server

提供地理编码、逆地理编码、路径规划、POI搜索、天气查询等功能。
基于高德地图 Web API v3，使用 FastMCP 实现。

启动方式:
    python mcp_servers/amap_server.py
    或通过 start-windows.bat 一键启动全部服务
"""

import functools
import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

# 加载项目根目录的 .env 文件
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("AMap_MCP_Server")

mcp = FastMCP("AMap")

# 高德 API Key — 从 .env 中加载
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")
AMAP_BASE_URL = "https://restapi.amap.com/v3"


def log_tool_call(func):
    """记录工具调用的日志"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        method_name = func.__name__
        logger.info(f"{'=' * 60}")
        logger.info(f"调用方法: {method_name}")
        try:
            params_str = json.dumps(kwargs, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            params_str = str(kwargs)
        logger.info(f"参数:\n{params_str}")

        try:
            result = await func(*args, **kwargs)
            logger.info("返回: SUCCESS")
            if isinstance(result, dict):
                summary = {
                    k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__}>"
                    for k, v in list(result.items())[:5]
                }
                logger.info(f"结果摘要: {json.dumps(summary, ensure_ascii=False)}")
            logger.info(f"{'=' * 60}")
            return result
        except Exception as e:
            logger.error(f"返回: ERROR — {e}")
            logger.error(f"{'=' * 60}")
            raise

    return wrapper


async def _call_amap_api(endpoint: str, params: dict) -> dict:
    """调用高德地图 API。

    Args:
        endpoint: API 路径，如 "/geocode/geo"
        params: 查询参数（不含 key）

    Returns:
        高德 API 响应 JSON
    """
    if not AMAP_API_KEY:
        return {"status": "0", "info": "AMAP_API_KEY 未配置，请设置环境变量 AMAP_API_KEY"}

    params["key"] = AMAP_API_KEY
    url = f"{AMAP_BASE_URL}/{endpoint.lstrip('/')}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ── 工具定义 ──────────────────────────────────────────────


@mcp.tool()
@log_tool_call
async def geocode(address: str, city: str = "") -> dict:
    """地理编码：将地址转换为经纬度坐标。

    Args:
        address: 结构化地址，如 "北京市朝阳区阜通东大街6号"
        city: 可选的城市名，缩小搜索范围

    Returns:
        dict: 包含经纬度、地址等信息
            - status: "1" 成功 / "0" 失败
            - geocodes: 匹配结果列表
            - count: 结果数量
    """
    params = {"address": address}
    if city:
        params["city"] = city
    return await _call_amap_api("geocode/geo", params)


@mcp.tool()
@log_tool_call
async def reverse_geocode(location: str) -> dict:
    """逆地理编码：将经纬度转换为地址。

    Args:
        location: 经纬度坐标，格式 "经度,纬度"，如 "116.481488,39.990464"

    Returns:
        dict: 包含详细地址、AOI、POI 等信息
            - regeocode: 包含 formatted_address、addressComponent 等
    """
    return await _call_amap_api("geocode/regeo", {"location": location, "extensions": "all"})


@mcp.tool()
@log_tool_call
async def search_poi(
    keywords: str,
    city: str = "",
    types: str = "",
    offset: int = 20,
    page: int = 1,
) -> dict:
    """POI 搜索：搜索兴趣点（餐厅、酒店、银行、医院等）。

    Args:
        keywords: 搜索关键词，如 "酒店"、"加油站"
        city: 城市，支持城市名/adcode；留空则全国搜索
        types: POI 类型，如 "050000|060000"（餐饮|购物），空则不过滤
        offset: 每页记录数（默认 20，最大 25）
        page: 页码（从 1 开始）

    Returns:
        dict: 包含 pois 列表，每项有 name、location、address、tel 等字段
    """
    params = {
        "keywords": keywords,
        "offset": str(offset),
        "page": str(page),
        "extensions": "all",
    }
    if city:
        params["city"] = city
    if types:
        params["types"] = types
    return await _call_amap_api("place/text", params)


@mcp.tool()
@log_tool_call
async def plan_route(
    origin: str,
    destination: str,
    route_type: str = "driving",
    city: str = "",
) -> dict:
    """路径规划：计算两点之间的出行路线。

    Args:
        origin: 起点坐标 "经度,纬度"，如 "116.434307,39.90909"
        destination: 终点坐标 "经度,纬度"
        route_type: 出行方式 — "driving"(驾车)、"walking"(步行)、"cycling"(骑行)、"transit"(公交)
        city: 城市名/adcode（公交规划时必填）

    Returns:
        dict: 包含 route 信息（路径、距离、时间等）
    """
    endpoint_map = {
        "driving": "direction/driving",
        "walking": "direction/walking",
        "cycling": "direction/bicycle",
        "transit": "direction/transit/integrated",
    }
    endpoint = endpoint_map.get(route_type, "direction/driving")

    params = {"origin": origin, "destination": destination}
    if route_type == "transit" and city:
        params["city"] = city
    if route_type in ("driving", "walking", "cycling"):
        params["show_fields"] = "cost,duration,navi,tmcs"

    return await _call_amap_api(endpoint, params)


@mcp.tool()
@log_tool_call
async def query_weather(city: str, extensions: str = "base") -> dict:
    """天气查询：查询指定城市的天气信息。

    Args:
        city: 城市 adcode（区划编码），如 "110000"（北京）
        extensions: "base" 实时天气 / "all" 预报天气（含未来 4 天）

    Returns:
        dict: 天气信息，含温度、湿度、风力等
    """
    return await _call_amap_api("weather/weatherInfo", {"city": city, "extensions": extensions})


@mcp.tool()
@log_tool_call
async def calculate_distance(
    origins: str,
    destination: str,
    measure_type: str = "1",
) -> dict:
    """距离测量：计算多个起点到终点的直线/驾车距离。

    Args:
        origins: 起点坐标列表，多项以 "|" 分隔，如 "116.481028,39.989643|116.434446,39.90816"
        destination: 终点坐标 "经度,纬度"
        measure_type: "0"=直线距离 / "1"=驾车距离（默认） / "2"=步行距离 / "3"=骑行距离

    Returns:
        dict: 包含 results 列表，每项有 distance、duration 等
    """
    return await _call_amap_api("distance", {
        "origins": origins,
        "destination": destination,
        "type": measure_type,
    })


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8005, path="/mcp")
