# OnCall

> 智能运维助手 — 把日志查询和监控诊断的活交给 Agent 去跑，我在旁边喝茶。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-0.1+-orange.svg)](https://www.langchain.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.0.40+-purple.svg)](https://langchain-ai.github.io/langgraph/)

## 这是什么

一个给运维团队用的对话式 Agent 系统。接入了日志平台和 Prometheus 监控数据，让大模型能直接帮你查日志、看指标、诊断故障。有两个核心能力：

- **RAG 问答** — 上传运维文档后，Agent 基于文档回答问题，问不到的内容拒绝编造。
- **AIOps 诊断** — 给定一个告警，Agent 自动制定排查计划、调用工具搜索日志和监控数据、输出诊断报告。

### 界面一览

| 首页对话 | 高德地图 MCP | 腾讯云 CLS MCP | AIOps 诊断 |
|---|---|---|---|
| ![首页](static/imgs/homepage.png) | ![高德地图](static/imgs/amap-mcp.png) | ![CLS](static/imgs/cls-mcp.png) | ![AIOps](static/imgs/aiops-diagnosis.png) |

## 技术选型 & 踩过的坑

| 层 | 选型 | 为什么 |
|---|---|---|
| 框架 | FastAPI + SSE | 诊断流程可能要跑几十秒，必须流式输出，不能让前端干等。 |
| 模型 | Qwen-Max (DashScope) | OpenAI 兼容协议，切模型不需要改代码。温度设 0.7，诊断时降为 0 降低幻觉。 |
| Agent 编排 | LangGraph | 最开始用 LangChain 的 AgentExecutor，发现工具调用失败后不会重试，排查流程没法中途调整。换了 LangGraph 的状态图，Plan → Execute → Replan 三阶段，每一步都能 inspect。 |
| 工具协议 | MCP (Model Context Protocol) | 工具和 Agent 之间用 MCP 解耦。CLS 日志查询、Monitor 监控、高德地图各自是独立的 MCP Server，Agent 通过 MCP Client 调用。后面换数据源，Agent 代码不用改。 |
| 向量库 | Milvus 2.3+ | 支持稠密+稀疏混合检索。一开始只用了 COSINE 相似度，发现关键词匹配场景（比如搜 "CPU 100%"）效果不好，补了 BM25 稀疏向量做 RRF 融合。 |
| 重排序 | qwen3-rerank | 粗排召回 10 条 → 精排取 Top 3。相似度 < 0.5 的直接丢弃，避免拿不相关文档去问模型。重排服务挂了有降级：单文档跳过 → API 异常回退 → 全局开关关闭。 |
| 会话存储 | SQLite (AsyncSqliteSaver) | 最早用 MemorySaver，服务重启对话全丢。迁移到 SqliteSaver 后会话持久化，`data/oncall_sessions.db` 一个文件搞定。 |
| 速率限制 | slowapi | 按端点分级：对话 10/min，流式对话 5/min，AIOps 3/min。因为 DashScope API 是付费的，不加限制一个死循环能把额度刷光。 |
| 上下文压缩 | 自写中间件 | LangChain 内置的 SummarizationMiddleware 依赖模型 profile 属性，Qwen 没有。自己写了个 before_model 中间件：QwenTokenizer 精确计数 → 超过 70% 窗口触发压缩 → 失败降级 trim 截断。 |

## 项目结构

```
OnCall/
├── app/
│   ├── main.py                        # FastAPI 入口，lifespan 里连 Milvus 初始化 BM25
│   ├── config.py                      # 全部配置项 + 默认值（Pydantic Settings）
│   ├── api/                           # 路由层，只做参数校验和响应格式化
│   │   ├── chat.py                    #   POST /api/chat, /api/chat_stream, DELETE /api/chat/clear
│   │   ├── aiops.py                   #   POST /api/aiops  SSE 流式诊断
│   │   ├── file.py                    #   POST /api/upload  文档上传入库
│   │   └── health.py                  #   GET  /api/health
│   ├── services/                      # 业务逻辑都在这里
│   │   ├── rag_agent_service.py       #   Agent 核心：create_agent + 中间件 + 工具注册
│   │   ├── aiops_service.py           #   Plan-Execute-Replan 诊断流程编排
│   │   ├── context_compressor.py      #   上下文压缩中间件（自实现）
│   │   ├── document_splitter_service.py # 文档切分：Markdown 三阶段 / PDF / Word
│   │   ├── vector_embedding_service.py  # 稠密 (text-embedding-v4) + 稀疏 (BM25)
│   │   ├── vector_index_service.py      # Milvus 索引管理 + Schema 迁移
│   │   ├── vector_search_service.py     # 混合检索：稠密 + 稀疏 RRF 融合 → 重排序
│   │   └── vector_store_manager.py      # Collection 生命周期管理
│   ├── agent/                         # Agent 专属模块
│   │   ├── mcp_client.py              #   MCP 多服务客户端（单例 + 指数退避重试）
│   │   └── aiops/                     #   Plan-Execute-Replan 三节点
│   │       ├── planner.py             #     查询知识库经验 → 生成诊断步骤
│   │       ├── executor.py            #     逐步执行，失败传错误给 Replanner
│   │       ├── replanner.py           #     评估结果，决定继续/调整/出报告
│   │       ├── state.py               #     状态 TypedDict 定义
│   │       └── utils.py               #     工具描述格式化
│   ├── core/                          # 基础设施
│   │   ├── llm_factory.py             #   ChatQwen 实例工厂
│   │   ├── milvus_client.py           #   Milvus 连接管理（单例）
│   │   └── rate_limiter.py            #   slowapi 包装器
│   ├── models/                        # Pydantic 请求/响应模型
│   └── utils/
│       └── logger.py                  # Loguru 配置：按天轮转 + 控制台彩色输出
├── static/                            # 前端：纯 HTML + vanilla JS + CSS
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── imgs/                           #   界面截图
├── mcp_servers/                       # MCP 工具服务（独立进程）
│   ├── cls_server.py                  #   日志查询服务 (FastMCP)
│   ├── monitor_server.py              #   监控数据服务 (Prometheus)
│   └── amap_server.py                 #   高德地图服务 (地理编码/路径规划/天气)
├── aiops-docs/                        # 运维知识库 Markdown 文档
├── data/                              # 运行时数据（SQLite 会话库等）
├── logs/                              # Loguru 日志输出
├── uploads/                           # 上传文件临时目录
├── volumes/                           # Milvus 数据持久化
├── .env                               # 环境变量（需手动创建）
├── Makefile                           # Linux/macOS 项目管理
├── start-windows.bat                  # Windows 启动脚本
├── stop-windows.bat                   # Windows 停止脚本
├── vector-database.yml                # Milvus Docker Compose
├── pyproject.toml                     # 依赖 + black/ruff/mypy/pytest 配置
└── README.md
```

## 快速开始

### 前置条件

- Python 3.11+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) （跑 Milvus 用）
- [DashScope API Key](https://dashscope.aliyun.com/) （阿里云百炼平台注册就能拿）

### 安装启动

**Linux / macOS：**

```bash
git clone <your-repo-url>
cd OnCall

# 装依赖（推荐 uv，比 pip 快一个数量级）
pip install uv
uv venv && source .venv/bin/activate
uv pip install -e .

# 编辑 .env，填上你的 DASHSCOPE_API_KEY
cp .env.example .env   # 如果没有 .env.example，手动创建，格式见下方配置说明
vim .env

# 一键初始化（拉 Docker 镜像 + 启 Milvus + 启服务 + 上传文档）
make init

# 后续启动只需
make start
```

**Windows（PowerShell）：**

```powershell
git clone <your-repo-url>
cd OnCall

# 装依赖
pip install uv
uv venv
.venv\Scripts\activate
uv pip install -e .

# 编辑 .env，填 DASHSCOPE_API_KEY
notepad .env

# 方式一：一键脚本
.\start-windows.bat

# 方式二：手动一步步起（方便排查问题）
# 终端1：启动 Milvus
docker compose -f vector-database.yml up -d

# 终端2：启动日志 MCP 服务
python mcp_servers/cls_server.py

# 终端3：启动监控 MCP 服务
python mcp_servers/monitor_server.py

# 终端4：启动高德地图 MCP 服务
python mcp_servers/amap_server.py

# 终端5：启动主服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900

# 上传知识库文档（主服务起来后再跑）
python -c "import requests, os; [requests.post('http://localhost:9900/api/upload', files={'file': open(f'aiops-docs/{f}', 'rb')}) for f in os.listdir('aiops-docs') if f.endswith('.md')]"
```

### 访问

| 地址 | 内容 |
|---|---|
| http://localhost:9900 | Web 对话界面 |
| http://localhost:9900/docs | Swagger API 文档 |

## API

### 接口总览

| 方法 | 路径 | 说明 | 限流 |
|---|---|---|---|
| POST | `/api/chat` | 普通对话，一次性返回 | 10/min |
| POST | `/api/chat_stream` | 流式对话，SSE | 5/min |
| POST | `/api/aiops` | AIOps 诊断，SSE 流式 | 3/min |
| POST | `/api/upload` | 上传文档到知识库 | 20/min |
| GET | `/api/health` | 健康检查 | - |

### 调用示例

```bash
# 普通对话
curl -X POST "http://localhost:9900/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-001","Question":"生产环境 CPU 飙高怎么排查？"}'

# 流式对话（SSE）
curl -X POST "http://localhost:9900/api/chat_stream" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-001","Question":"帮我查下最近的错误日志"}' \
  --no-buffer

# AIOps 诊断
curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"session-001"}' \
  --no-buffer
```

SSE 事件类型：`content`（文本片段）、`tool_call`（工具调用开始/结束）、`search_results`（检索结果）、`done`（完成）、`error`（错误）。

## 配置

所有配置项通过 `.env` 文件管理，有默认值的可以省略：

```bash
# 必填
DASHSCOPE_API_KEY=sk-your-key-here
# 如果用国内站点，需要指定（默认走新加坡）
DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-max

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530

# RAG
RAG_TOP_K=10                  # 粗排召回条数
RAG_SCORE_THRESHOLD=0.5       # 相似度过滤阈值
RERANK_ENABLED=true           # 重排序开关
RERANK_TOP_N=3                # 精排保留条数
HYBRID_SEARCH_ENABLED=true    # 混合检索开关（稠密+稀疏）

# 文档分块
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# 上下文压缩
CONTEXT_COMPRESSION_ENABLED=true
CONTEXT_COMPRESSION_TRIGGER_FRACTION=0.7  # 达到窗口 70% 触发压缩

# 会话持久化
CHECKPOINT_DB_PATH=data/oncall_sessions.db

# 速率限制
RATE_LIMIT_ENABLED=true
RATE_LIMIT_CHAT=10/minute
RATE_LIMIT_CHAT_STREAM=5/minute
RATE_LIMIT_AIOP=3/minute
RATE_LIMIT_UPLOAD=20/minute

# MCP 服务地址
MCP_CLS_URL=http://localhost:8003/mcp
MCP_MONITOR_URL=http://localhost:8004/mcp
MCP_AMAP_URL=http://localhost:8005/mcp

# 高德地图（前往 https://lbs.amap.com/ 申请 Key）
AMAP_API_KEY=your-amap-key

# Prometheus
PROMETHEUS_BASE_URL=http://localhost:9090
```

## AIOps 诊断流程

基于 LangGraph 的 **Plan-Execute-Replan** 模式，完整流程：

```
用户发起诊断
    ↓
1. Planner — 查知识库找相关运维经验 → 分析有什么工具可用 → 生成 4~6 步诊断计划
    ↓
2. Executor — 逐步执行计划，调用 MCP 工具（查日志、拉 Prometheus 指标）
    ↓       ← 失败？传错误信息给 Replanner
3. Replanner — 评估当前结果，决定：继续下一步 / 调整剩余计划 / 直接生成报告
    ↓
4. 输出结构化诊断报告（根因分析 + 关键证据 + 运维建议）
```

保护机制：
- 最多 8 步，超过 5 步后禁止重规划，防止无限循环。
- Executor 单步失败不中断，错误信息传给 Replanner 判断要不要重试。
- 整个过程 SSE 流式推给前端，能看到每一步的执行状态。

## 知识库支持的文件类型

| 类型 | 处理方式 |
|---|---|
| `.md` | Markdown 三段切分：按标题切 → 长段落递归切 → 碎片合并（保证 chunk 在 800 字以内且不切断句子） |
| `.txt` | 按段落 + 长度切分 |
| `.pdf` | pdfplumber 提取文本后切分 |
| `.docx` | python-docx 提取文本后切分 |

新增文件类型只需加一个 Handler 类，注册到 HandlerRegistry，不用改已有代码。

## 常见问题

### Windows 相关

**`make` 命令不可用：**
```powershell
.\start-windows.bat   # 用批处理替代
```

**PowerShell 脚本执行报错：**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
# 或者直接用 CMD 跑 .bat
```

**端口被占用：**
```powershell
netstat -ano | findstr :9900
taskkill /F /PID <PID>
```

### 通用问题

**DashScope API Key 报错：**
```bash
# 确认 .env 里填了 key（不是示例值）
cat .env | grep DASHSCOPE_API_KEY     # Linux/macOS
type .env | findstr DASHSCOPE_API_KEY  # Windows
```

**Milvus 连不上：**
```bash
# 确保 Docker Desktop 在运行
docker ps | grep milvus

# 没有就启动
docker compose -f vector-database.yml up -d

# 起来了但连不上，重启 standalone 容器
docker compose -f vector-database.yml restart standalone
```

**服务启动失败，想查日志：**
```bash
# Linux/macOS
tail -f logs/app_$(date +%Y-%m-%d).log   # 主服务
tail -f mcp_cls.log                       # CLS MCP
tail -f mcp_monitor.log                   # Monitor MCP

# Windows PowerShell
$today = Get-Date -Format "yyyy-MM-dd"
Get-Content logs\app_$today.log -Tail 50
```

## License

MIT
