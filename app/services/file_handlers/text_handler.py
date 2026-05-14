"""纯文本文件处理器 (.txt)"""

from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from app.config import config
from app.services.file_handlers.base import BaseFileHandler


class TextHandler(BaseFileHandler):
    """纯文本文件处理器

    分片策略（单级分片）：
    - 直接使用 RecursiveCharacterTextSplitter 按照自然段落边界切分
    - chunk_size = 1600 字符（config.chunk_max_size * 2）
    - chunk_overlap = 100 字符
    - 分隔符优先级：双换行 → 单换行 → 空格 → 字符级
    """

    @property
    def supported_extensions(self) -> List[str]:
        return ["txt"]

    def __init__(self):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_max_size * 2,
            chunk_overlap=config.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

    def extract_text(self, file_path: str) -> str:
        path = Path(file_path)
        return path.read_text(encoding="utf-8")

    def split(self, content: str, file_path: str = "") -> List[Document]:
        if not content or not content.strip():
            logger.warning(f"文本文档内容为空: {file_path}")
            return []

        docs = self._splitter.create_documents(
            texts=[content],
            metadatas=[{
                "_source": file_path,
                "_extension": ".txt",
                "_file_name": Path(file_path).name,
            }],
        )

        logger.info(f"文本分割完成: {file_path} -> {len(docs)} 个分片")
        return docs
