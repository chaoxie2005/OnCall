"""BM25 稀疏向量嵌入服务

封装 pymilvus.model.sparse.BM25EmbeddingFunction，为混合检索提供
BM25 稀疏向量编码能力。模型从 Milvus 现有文档语料拟合，支持
持久化到磁盘以减少重启时间。
"""

from pathlib import Path

from loguru import logger
from milvus_model.sparse import BM25EmbeddingFunction

from app.config import config


class BM25EmbeddingService:
    """BM25 稀疏向量嵌入服务

    延迟拟合：首次调用 encode_* 且模型未就绪时自动从 Milvus
    加载语料拟合。拟合完成后将模型持久化到磁盘，下次启动直接加载。
    """

    def __init__(self):
        self._bm25: BM25EmbeddingFunction | None = None
        self._model_path = Path(config.checkpoint_db_path).parent / "bm25_model.pkl"

    @property
    def is_fitted(self) -> bool:
        return self._bm25 is not None

    def _try_load_from_disk(self) -> bool:
        try:
            if self._model_path.exists():
                self._bm25 = BM25EmbeddingFunction.load(str(self._model_path))
                logger.info(f"从磁盘加载 BM25 模型: {self._model_path}")
                return True
        except Exception as e:
            logger.warning(f"从磁盘加载 BM25 模型失败: {e}")
        return False

    def fit(self, corpus: list[str]) -> None:
        """拟合 BM25 模型并在拟合成功后持久化到磁盘。

        Args:
            corpus: 文档内容字符串列表，用于计算 IDF。
        """
        if not corpus:
            logger.warning("BM25 拟合语料为空，跳过")
            return

        logger.info(f"开始拟合 BM25 模型，语料大小: {len(corpus)}")
        self._bm25 = BM25EmbeddingFunction()
        self._bm25.fit(corpus)

        try:
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            self._bm25.save(str(self._model_path))
            logger.info(f"BM25 模型已保存到: {self._model_path}")
        except Exception as e:
            logger.warning(f"BM25 模型持久化失败（不影响服务）: {e}")

    def encode_documents(self, texts: list[str]) -> list:
        """编码批量文档文本为稀疏向量。

        Args:
            texts: 文档文本列表

        Returns:
            稀疏向量列表，每个元素为 scipy.sparse.csr_matrix 的一行
        """
        if not self._bm25:
            raise RuntimeError("BM25 模型未拟合，请先调用 fit() 或从磁盘加载")
        return self._bm25.encode_documents(texts)

    def encode_query(self, text: str) -> list:
        """编码单条查询文本为稀疏向量。

        Args:
            text: 查询文本

        Returns:
            稀疏向量（scipy.sparse 格式）
        """
        if not self._bm25:
            raise RuntimeError("BM25 模型未拟合，请先调用 fit() 或从磁盘加载")
        return self._bm25.encode_queries([text])


bm25_embedding_service = BM25EmbeddingService()
