"""混合检索服务 - 稠密向量 + BM25 稀疏向量两路召回 + RRF 融合

使用 pymilvus 原生 hybrid_search() API 在 Milvus 内部完成检索，
不依赖 langchain-milvus 的检索器封装。
"""

from collections.abc import Sequence

from langchain_core.documents import Document
from loguru import logger
from pymilvus import AnnSearchRequest, RRFRanker

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.bm25_embedding_service import bm25_embedding_service
from app.services.vector_embedding_service import vector_embedding_service


class HybridSearchService:
    """混合检索服务

    稠密向量（COSINE 语义匹配）+ BM25 稀疏向量（关键词匹配）
    通过 RRF 融合两路结果，返回 LangChain Document 列表。
    """

    def search(self, query: str, top_k: int | None = None) -> Sequence[Document]:
        """执行混合检索

        Args:
            query: 查询文本
            top_k: 融合后返回的文档数，None 则使用配置默认值

        Returns:
            按 RRF 分数降序排列的 Document 列表
        """
        final_top_k = top_k or config.hybrid_final_top_k

        # 1. 生成双路查询向量
        dense_emb: list[float] = vector_embedding_service.embed_query(query)
        sparse_emb = bm25_embedding_service.encode_query(query)

        # 2. 构建稠密搜索请求
        dense_req = AnnSearchRequest(
            data=[dense_emb],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"nprobe": 10}},
            limit=config.hybrid_dense_top_k,
        )

        # 3. 构建稀疏搜索请求
        sparse_req = AnnSearchRequest(
            data=[sparse_emb],
            anns_field="sparse_vector",
            param={"metric_type": "IP"},
            limit=config.hybrid_sparse_top_k,
        )

        # 4. 执行混合搜索 + RRF 融合
        collection = milvus_manager.get_collection()
        results = collection.hybrid_search(
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=config.hybrid_rrf_k),
            limit=final_top_k,
            output_fields=["id", "content", "metadata"],
        )

        # 5. 解析结果
        docs = self._parse_results(results)

        logger.info(
            f"混合检索完成: query='{query[:60]}...', "
            f"dense_k={config.hybrid_dense_top_k}, "
            f"sparse_k={config.hybrid_sparse_top_k}, "
            f"rrf_k={config.hybrid_rrf_k}, "
            f"最终结果数={len(docs)}"
        )
        return docs

    def _parse_results(self, results: list) -> list[Document]:
        """解析 hybrid_search 返回结果为 LangChain Document 列表"""
        docs: list[Document] = []
        for hits in results:
            for hit in hits:
                entity = hit.entity if hasattr(hit, "entity") else hit
                metadata = {
                    **(entity.get("metadata") or {}),
                    "_rrf_score": hit.score if hasattr(hit, "score") else None,
                }
                doc = Document(
                    page_content=entity.get("content", ""),
                    metadata=metadata,
                )
                docs.append(doc)
        return docs


hybrid_search_service = HybridSearchService()
