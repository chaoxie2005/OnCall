"""文档分割服务模块 - 基于 LangChain 的多格式文档智能分割

根据上传文件类型自动匹配到对应的文件处理器进行分片操作。
支持的文件类型：md, txt, pdf, docx, pptx

各类型分片策略详见 app/services/file_handlers/ 下对应的处理器文件。
"""

from pathlib import Path
from typing import List

from langchain_core.documents import Document
from loguru import logger

from app.services.file_handlers import (
    FileHandlerRegistry,
    MarkdownHandler,
    PDFHandler,
    PptxHandler,
    TextHandler,
    WordHandler,
)


def _build_default_registry() -> FileHandlerRegistry:
    """构建并初始化默认的处理器注册中心"""
    registry = FileHandlerRegistry()
    registry.register(MarkdownHandler())
    registry.register(TextHandler())
    registry.register(PDFHandler())
    registry.register(WordHandler())
    registry.register(PptxHandler())
    return registry


class DocumentSplitterService:
    """文档分割服务 - 委托给文件类型处理器进行分片

    保持向后兼容：对外接口不变，内部委托给 FileHandlerRegistry 分发。
    """

    def __init__(self, registry: FileHandlerRegistry = None):
        self._registry = registry or _build_default_registry()
        logger.info(
            f"文档分割服务初始化完成，已注册处理器: "
            f"{[h.__class__.__name__ for h in set(self._registry.handlers.values())]}, "
            f"支持扩展名: {self._registry.get_allowed_extensions()}"
        )

    def split_document(self, content: str, file_path: str = "") -> List[Document]:
        """根据文件扩展名自动选择处理器进行分片"""
        handler = self._registry.get_handler(file_path)
        if handler is None:
            logger.warning(f"未找到匹配的处理器，回退到 TextHandler: {file_path}")
            handler = self._registry.get_handler("dummy.txt")

        return handler.split(content, file_path)


# 全局单例（保持向后兼容）
document_splitter_service = DocumentSplitterService()
