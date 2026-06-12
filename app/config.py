"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "SuperBizAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # DashScope 配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 10
    rag_score_threshold: float = 0.5
    rag_model: str = "qwen-max"  # 使用快速响应模型，不带扩展思考

    # Hybrid search 配置
    hybrid_search_enabled: bool = True
    hybrid_dense_top_k: int = 10
    hybrid_sparse_top_k: int = 10
    hybrid_rrf_k: int = 60
    hybrid_final_top_k: int = 10

    # Rerank 配置
    rerank_enabled: bool = True
    rerank_model: str = "qwen3-rerank"
    rerank_top_n: int = 3

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # 上下文压缩配置
    context_compression_enabled: bool = True
    context_compression_trigger_fraction: float = 0.7  # 70% 时触发压缩
    context_compression_keep_recent: int = 6  # 压缩后保留最近消息数（3轮对话）
    context_compression_model_window: int = 32768  # qwen-max 上下文窗口

    # 会话持久化配置（SQLite）
    checkpoint_db_path: str = "data/oncall_sessions.db"

    # 速率限制配置
    rate_limit_enabled: bool = True
    rate_limit_chat: str = "10/minute"
    rate_limit_chat_stream: str = "5/minute"
    rate_limit_aiops: str = "5/minute"
    rate_limit_upload: str = "20/minute"
    rate_limit_storage_uri: str = "memory://"
    rate_limit_headers_enabled: bool = True

    # MCP 服务配置
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    # Prometheus 配置
    prometheus_base_url: str = "http://localhost:9090"
    prometheus_timeout: int = 10
    prometheus_alert_states: list[str] = []  # 过滤告警状态，空列表=不过滤；示例：["firing"]

    @property
    def mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }


# 全局配置实例
config = Settings()


