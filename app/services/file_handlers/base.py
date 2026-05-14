"""文件处理器抽象基类"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from langchain_core.documents import Document


class BaseFileHandler(ABC):
    """文件类型处理器的抽象基类

    每种文件类型对应一个具体处理器，负责：
    1. 从原始文件中提取纯文本内容
    2. 将文本内容分片为适合向量化的 Document 列表
    """

    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """返回该处理器支持的文件扩展名列表（小写，不含点），如 ['md', 'markdown']"""
        ...

    @abstractmethod
    def extract_text(self, file_path: str) -> str:
        """从文件提取纯文本内容

        Args:
            file_path: 文件路径

        Returns:
            提取后的纯文本字符串
        """
        ...

    @abstractmethod
    def split(self, content: str, file_path: str = "") -> List[Document]:
        """将文本内容分片为 Document 列表

        Args:
            content: 纯文本内容（由 extract_text 提取）
            file_path: 文件路径（用于写入 Document 元数据）

        Returns:
            Document 列表，每个 Document 包含 page_content 和 metadata
        """
        ...

    def can_handle(self, file_path: str) -> bool:
        """判断此处理器是否能处理给定文件"""
        ext = Path(file_path).suffix.lower().lstrip(".")
        return ext in self.supported_extensions
