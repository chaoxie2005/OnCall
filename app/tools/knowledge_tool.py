"""知识检索工具 - 从向量数据库中检索相关信息，并通过百炼重排模型精排结果"""

from http import HTTPStatus
from typing import List, Tuple

import dashscope
from langchain_core.documents import Document
from langchain_core.tools import tool
from loguru import logger

from app.config import config
from app.services.vector_store_manager import vector_store_manager


def rerank_documents(query: str, docs: List[Document], top_n: int) -> List[Document]:
    """使用阿里云百炼 qwen3-rerank 模型对召回文档进行语义重排序

    调用 DashScope TextReRank API，根据文档与查询的语义相关性重新排序，
    返回相关性最高的 top_n 个文档。失败时降级返回原始排序，确保不影响可用性。

    Args:
        query: 用户查询文本
        docs: 向量检索召回的文档列表
        top_n: 返回的文档数量

    Returns:
        List[Document]: 按相关性降序排列的 top_n 个文档
    """
    if len(docs) <= 1:
        return docs

    try:
        documents_text = [doc.page_content for doc in docs]

        resp = dashscope.TextReRank.call(
            model=config.rerank_model,
            query=query,
            documents=documents_text,
            top_n=top_n,
            return_documents=False,
            api_key=config.dashscope_api_key,
        )

        if resp.status_code != HTTPStatus.OK:
            logger.warning(
                f"重排 API 返回非 200 状态码: {resp.status_code}, "
                f"message: {resp.message}, 使用原始排序"
            )
            return docs[:top_n]

        results = resp.output.results
        if not results:
            logger.warning("重排 API 返回空结果，使用原始排序")
            return docs[:top_n]

        reranked = [docs[item.index] for item in results]
        logger.info(
            f"重排序完成: {len(docs)} -> {len(reranked)} 个文档, "
            f"top_score={results[0].relevance_score:.4f}"
        )
        return reranked

    except Exception as e:
        logger.error(f"重排序调用失败，降级使用原始排序: {e}")
        return docs[:top_n]


@tool(response_format="content_and_artifact")
def retrieve_knowledge(query: str) -> Tuple[str, List[Document]]:
    """从知识库中检索相关信息来回答问题
    
    当用户的问题涉及专业知识、文档内容或需要参考资料时，使用此工具。
    
    Args:
        query: 用户的问题或查询
        
    Returns:
        Tuple[str, List[Document]]: (格式化的上下文文本, 原始文档列表)
    """
    try:
        logger.info(f"知识检索工具被调用: query='{query}'")
        
        # 从向量存储中检索相关文档
        vector_store = vector_store_manager.get_vector_store()
        retriever = vector_store.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={
                "k": config.rag_top_k,
                "score_threshold": config.rag_score_threshold,
            },
        )
        
        docs = retriever.invoke(query)

        if not docs:
            logger.warning("未检索到相关文档")
            return "没有找到相关信息。", []

        # 语义重排序：使用百炼 qwen3-rerank 对召回文档精排
        if config.rerank_enabled:
            docs = rerank_documents(query, docs, config.rerank_top_n)

        # 格式化文档为上下文
        context = format_docs(docs)

        logger.info(f"检索到 {len(docs)} 个相关文档")
        return context, docs
        
    except Exception as e:
        logger.error(f"知识检索工具调用失败: {e}")
        return f"检索知识时发生错误: {str(e)}", []


def format_docs(docs: List[Document]) -> str:
    """
    格式化文档列表为上下文文本
    
    Args:
        docs: 文档列表
        
    Returns:
        str: 格式化的上下文文本
    """
    formatted_parts = []
    
    for i, doc in enumerate(docs, 1):
        # 提取元数据
        metadata = doc.metadata
        source = metadata.get("_file_name", "未知来源")
        
        # 提取标题信息 (如果有)
        headers = []
        for key in ["h1", "h2", "h3"]:
            if key in metadata and metadata[key]:
                headers.append(metadata[key])
        
        header_str = " > ".join(headers) if headers else ""
        
        # 构建格式化文本
        formatted = f"【参考资料 {i}】"
        if header_str:
            formatted += f"\n标题: {header_str}"
        formatted += f"\n来源: {source}"
        formatted += f"\n内容:\n{doc.page_content}\n"
        
        formatted_parts.append(formatted)
    
    return "\n".join(formatted_parts)
