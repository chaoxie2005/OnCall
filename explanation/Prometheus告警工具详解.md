# Prometheus 告警查询工具详解

## 概述

`query_prometheus_alerts` 是一个 LangChain 原生工具，用于实时获取 Prometheus AlertManager 的活跃告警信息，为大模型提供故障诊断的第一手数据。

该工具直接调用 Prometheus 官方告警查询接口 `GET /api/v1/alerts`，从 JSON 响应中解析关键字段并结构化输出。

## 文件位置

- **工具实现**: [app/tools/prometheus_tool.py](../app/tools/prometheus_tool.py)
- **配置**: [app/config.py](../app/config.py)（Prometheus 相关配置项）
- **注册**: [app/tools/__init__.py](../app/tools/__init__.py)

## 核心功能

### 1. 告警数据获取

调用 Prometheus HTTP API（`/api/v1/alerts`），获取完整的告警列表。

### 2. 关键字段提取

从 API 响应的 JSON 中解析以下字段：

| 字段 | 来源 | 说明 | 示例 |
|------|------|------|------|
| `alertname` | `labels.alertname` | 告警名称 | `ServiceDown` |
| `instance` | `labels.instance` | 受影响实例 | `10.0.1.5:8080` |
| `severity` | `labels.severity` | 告警级别 | `critical` |
| `description` | `annotations.description` | 告警详情描述 | `广告微服务下线，超过5分钟无响应` |
| `status` | `state` | 告警状态 | `firing` |
| `activeAt` | `activeAt` | 触发时间 | `2025-10-29T08:48:42Z` |

### 3. 状态过滤

支持按告警状态过滤（通过 `prometheus_alert_states` 配置项），默认不过滤，返回全部告警。配置为 `["firing"]` 时仅返回活跃告警。

### 4. 结构化输出

将多字段信息组织为可读文本，格式如下：

```
共 2 条活跃告警：

【告警 1】
  告警名称: ServiceDown
  告警级别: critical
  详情描述: 广告微服务下线，超过5分钟无响应
  影响实例: 10.0.1.5:8080
  触发时间: 2025-10-29T08:48:42Z
  状态: firing

【告警 2】
  告警名称: HighCPUUsage
  告警级别: warning
  详情描述: 数据库服务器CPU使用率超过85%
  影响实例: 10.0.1.10:9090
  触发时间: 2025-10-29T08:50:15Z
  状态: firing
```

## 配置说明

在 `.env` 文件中配置以下环境变量：

```bash
# Prometheus 服务地址（必填）
PROMETHEUS_BASE_URL=http://prometheus-server:9090

# 请求超时时间，单位秒（可选，默认 10）
PROMETHEUS_TIMEOUT=10

# 告警状态过滤，JSON 数组格式（可选，默认不过滤）
# 示例：只获取 firing 状态的告警
PROMETHEUS_ALERT_STATES=["firing"]

# 示例：只获取 firing 和 pending 状态的告警
# PROMETHEUS_ALERT_STATES=["firing","pending"]
```

## 在 Agent 中的使用

### RAG Agent（智能问答）

RAG Agent 在初始化时将 `query_prometheus_alerts` 绑定为可用工具。当用户提问涉及线上故障时，Agent 会自动调用该工具获取最新告警，结合知识库给出诊断建议。

```
用户: "服务下线了怎么办？"
→ Agent 调用 query_prometheus_alerts() 获取当前告警
→ Agent 调用 retrieve_knowledge("服务下线处理方案") 查知识库
→ Agent 综合告警 + 知识库，给出针对性处理建议
```

### AIOps Agent（故障诊断）

在 Plan-Execute-Replan 流程中：
- **Planner** 制定计划时可参考 Prometheus 告警制定诊断步骤
- **Executor** 执行时调用 `query_prometheus_alerts` 获取实时告警

## 故障处理与降级

| 异常类型 | 返回内容 | 日志级别 |
|----------|----------|----------|
| HTTP 非 200 | `"调用 Prometheus API 失败，HTTP {code}。"` | ERROR |
| 连接超时 | `"查询 Prometheus 告警超时，请稍后重试。"` | ERROR |
| 连接失败 | `"无法连接 Prometheus 服务，请检查网络和 Prometheus 地址配置。"` | ERROR |
| 其他异常 | `"查询 Prometheus 告警时发生错误: {msg}"` | ERROR |
| 无告警 | `"当前没有活跃的 Prometheus 告警。"` | INFO |

所有异常都会降级返回友好提示文本，不会中断 Agent 的对话流程。

## 架构图示

```
用户提问
    ↓
RAG Agent / AIOps Agent
    ↓
query_prometheus_alerts
    ↓
httpx.get(PROMETHEUS_BASE_URL/api/v1/alerts)
    ↓
Prometheus AlertManager
    ↓
JSON 解析 → 字段提取 → 结构化文本
    ↓
Agent 结合告警信息 + 知识库 → 生成诊断建议
```
