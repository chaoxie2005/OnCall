"""文件类型处理器包 - 支持多格式文档的文本提取与分片"""

from app.services.file_handlers.base import BaseFileHandler
from app.services.file_handlers.registry import FileHandlerRegistry, get_handler_for_file
from app.services.file_handlers.text_handler import TextHandler
from app.services.file_handlers.markdown_handler import MarkdownHandler
from app.services.file_handlers.pdf_handler import PDFHandler
from app.services.file_handlers.word_handler import WordHandler

__all__ = [
    "BaseFileHandler",
    "FileHandlerRegistry",
    "get_handler_for_file",
    "TextHandler",
    "MarkdownHandler",
    "PDFHandler",
    "WordHandler",
]
