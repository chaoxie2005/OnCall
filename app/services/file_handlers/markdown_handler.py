"""Markdown 文件处理器 (.md, .markdown)"""

from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger

from app.config import config
from app.services.file_handlers.base import BaseFileHandler


class MarkdownHandler(BaseFileHandler):
    """Markdown 文件处理器

    分片策略（三级分片）：

    第一级 — 按标题结构切分：
    - 使用 MarkdownHeaderTextSplitter 按 H1 (#) 和 H2 (##) 标题切分
    - 不再按 H3 及以下标题切分，避免过度碎片化
    - strip_headers=False，标题文本保留在分片内容中

    第二级 — 按大小进一步切分：
    - 使用 RecursiveCharacterTextSplitter 处理超出 chunk_size*2 的大块
    - chunk_size=1600, overlap=100

    第三级 — 合并过小碎片：
    - 将小于 300 字符的分片合并到相邻分片
    - 合并后不超过 chunk_size*2 则不限制
    """

    @property
    def supported_extensions(self) -> List[str]:
        return ["md", "markdown"]

    def __init__(self):
        chunk_size = config.chunk_max_size
        chunk_overlap = config.chunk_overlap

        self._markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
            ],
            strip_headers=False,
        )

        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size * 2,
            chunk_overlap=chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

        self._chunk_size = chunk_size

    def extract_text(self, file_path: str) -> str:
        """Markdown 文件本身就是纯文本，直接读取"""
        path = Path(file_path)
        return path.read_text(encoding="utf-8")

    def split(self, content: str, file_path: str = "") -> List[Document]:
        if not content or not content.strip():
            logger.warning(f"Markdown 文档内容为空: {file_path}")
            return []

        try:
            # 第一级：按标题切分
            md_docs = self._markdown_splitter.split_text(content)

            # 第二级：按大小进一步切分超大块
            docs_after_split = self._text_splitter.split_documents(md_docs)

            # 第三级：合并过小碎片
            final_docs = self._merge_small_chunks(docs_after_split, min_size=300)

            # 写入元数据
            for doc in final_docs:
                doc.metadata["_source"] = file_path
                doc.metadata["_extension"] = ".md"
                doc.metadata["_file_name"] = Path(file_path).name

            logger.info(f"Markdown 分割完成: {file_path} -> {len(final_docs)} 个分片")
            return final_docs

        except Exception as e:
            logger.error(f"Markdown 分割失败: {file_path}, 错误: {e}")
            raise

    def _merge_small_chunks(
        self, documents: List[Document], min_size: int = 300
    ) -> List[Document]:
        """合并太小的分片到相邻分片中"""
        if not documents:
            return []

        merged_docs = []
        current_doc = None

        for doc in documents:
            doc_size = len(doc.page_content)

            if current_doc is None:
                current_doc = doc
            elif doc_size < min_size and len(current_doc.page_content) < self._chunk_size * 2:
                current_doc.page_content += "\n\n" + doc.page_content
            else:
                merged_docs.append(current_doc)
                current_doc = doc

        if current_doc is not None:
            merged_docs.append(current_doc)

        return merged_docs
