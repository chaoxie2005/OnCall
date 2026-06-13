"""FastAPI 应用入口

主应用程序，配置路由、中间件、静态文件等
"""

# ⚠️ 必须在所有导入之前设置，否则 httpx 会读取 Windows 系统代理
# 导致本地 MCP 请求被发送到代理服务器 → 502 Bad Gateway
# 覆盖系统代理设置，直接连接本地服务
import os
for _k in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    if _k.upper().startswith("HTTP"):
        os.environ[_k] = ""  # 清空代理
    else:
        os.environ[_k] = "localhost,127.0.0.1,::1"  # 绕过代理

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os

from app.config import config
from loguru import logger
from app.api import chat, health, file, aiops
from app.core.milvus_client import milvus_manager
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded


def _initialize_bm25() -> None:
    """加载或从 Milvus 语料拟合 BM25 模型。

    优先从磁盘加载已持久化的模型；若不存在则从 Milvus
    收集现有文档作为语料进行拟合。Schema 迁移后自动重索引
    稀疏向量。
    """
    from app.services.bm25_embedding_service import bm25_embedding_service

    if bm25_embedding_service.is_fitted:
        logger.info("BM25 模型已在 VectorStoreManager 初始化阶段加载")
        return

    if not bm25_embedding_service._try_load_from_disk():
        try:
            collection = milvus_manager.get_collection()
            batch_size = 1000
            offset = 0
            corpus: list[str] = []
            while True:
                results = collection.query(
                    expr="",
                    output_fields=["content"],
                    limit=batch_size,
                    offset=offset,
                )
                if not results:
                    break
                corpus.extend(r["content"] for r in results if r.get("content"))
                offset += batch_size

            if corpus:
                bm25_embedding_service.fit(corpus)
                logger.info(f"BM25 模型从 Milvus 语料拟合完成，文档数: {len(corpus)}")
            else:
                logger.info("Milvus 中暂无文档，BM25 将在首次文档上传时延迟拟合")
        except Exception as e:
            logger.warning(f"BM25 初始化失败，将回退到纯稠密检索: {e}")

    # Schema 迁移后自动重索引稀疏向量
    if milvus_manager.schema_migrated and bm25_embedding_service.is_fitted:
        try:
            from app.services.vector_index_service import vector_index_service
            count = vector_index_service.reindex_sparse_vectors()
            logger.info(f"Schema 迁移后重索引完成，{count} 条文档已补充稀疏向量")
        except Exception as e:
            logger.warning(f"重索引失败（可稍后手动触发）: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 60)
    logger.info(f"🚀 {config.app_name} v{config.app_version} 启动中...")
    logger.info(f"📝 环境: {'开发' if config.debug else '生产'}")
    logger.info(f"🌐 监听地址: http://{config.host}:{config.port}")
    logger.info(f"📚 API 文档: http://{config.host}:{config.port}/docs")

    # 连接 Milvus
    logger.info("🔌 正在连接 Milvus...")
    milvus_manager.connect()
    logger.info("✅ Milvus 连接成功")

    # 初始化 BM25 混合检索
    if config.hybrid_search_enabled:
        _initialize_bm25()

    logger.info("=" * 60)

    yield

    # 关闭时执行
    logger.info("🔌 正在关闭 Milvus 连接...")
    milvus_manager.close()
    logger.info(f"👋 {config.app_name} 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 的智能oncall运维系统",
    lifespan=lifespan
)

# 注册速率限制器到 app 状态（slowapi 中间件依赖）
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request, exc):
    """速率限制触发时的响应"""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={
            "code": 429,
            "message": "请求过于频繁，请稍后重试",
            "data": {
                "retry_after_seconds": exc.retry_after if hasattr(exc, "retry_after") else 60,
            },
        },
    )


# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(aiops.router, prefix="/api", tags=["AIOps智能运维"])

# 挂载静态文件
static_dir = "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def root():
    """返回首页"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level="info"
    )
