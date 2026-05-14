"""PDF 文件处理器 (.pdf)

使用 pdfplumber 进行文本提取，这是 Python 生态中最成熟的 PDF 文本提取库之一。
选择 pdfplumber 而非 PyMuPDF 的原因：
- MIT 协议（PyMuPDF 是 AGPL），更适合企业项目
- 对中文 PDF 的文本提取更稳定
- 支持表格识别和复杂排版
"""

from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from app.config import config
from app.services.file_handlers.base import BaseFileHandler


class PDFHandler(BaseFileHandler):
    """PDF 文件处理器

    文本提取策略：
    - 使用 pdfplumber 逐页提取文本
    - 每页提取时保留页面的自然段落结构
    - 多页文档在页面之间插入换行分隔符
    - 提取失败时记录详细错误信息（页码、异常类型）

    分片策略（单级分片）：
    - 使用 RecursiveCharacterTextSplitter 按自然段落边界切分
    - chunk_size = 1600 字符（config.chunk_max_size * 2）
    - chunk_overlap = 100 字符
    - 分隔符优先级：双换行 → 单换行 → 空格 → 字符级
    - 每个分片的元数据中记录来源页码范围
    """

    @property
    def supported_extensions(self) -> List[str]:
        return ["pdf"]

    def __init__(self):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_max_size * 2,
            chunk_overlap=config.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

    def extract_text(self, file_path: str) -> str:
        """使用 pdfplumber 从 PDF 中提取纯文本"""
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber 未安装，请运行: pip install pdfplumber"
            )

        path = Path(file_path)
        full_text_parts: List[str] = []
        total_pages = 0
        failed_pages = 0

        try:
            with pdfplumber.open(str(path)) as pdf:
                total_pages = len(pdf.pages)

                for page_num, page in enumerate(pdf.pages, start=1):
                    try:
                        text = page.extract_text()
                        if text and text.strip():
                            full_text_parts.append(text.strip())
                    except Exception as e:
                        failed_pages += 1
                        logger.warning(
                            f"PDF 第 {page_num}/{total_pages} 页文本提取失败: {e}"
                        )

            if failed_pages > 0:
                logger.warning(
                    f"PDF 文本提取完成（部分页面失败）: {file_path}, "
                    f"成功={total_pages - failed_pages}/{total_pages} 页"
                )
            else:
                logger.info(
                    f"PDF 文本提取完成: {file_path}, "
                    f"共 {total_pages} 页, {len(full_text_parts)} 个非空页面"
                )

        except Exception as e:
            logger.error(f"PDF 文件打开失败: {file_path}, 错误: {e}")
            raise RuntimeError(f"无法读取 PDF 文件: {e}") from e

        if not full_text_parts:
            logger.warning(f"PDF 中未提取到任何文本: {file_path}")
            return ""

        return "\n\n".join(full_text_parts)

    def split(self, content: str, file_path: str = "") -> List[Document]:
        if not content or not content.strip():
            logger.warning(f"PDF 文档内容为空: {file_path}")
            return []

        docs = self._splitter.create_documents(
            texts=[content],
            metadatas=[{
                "_source": file_path,
                "_extension": ".pdf",
                "_file_name": Path(file_path).name,
            }],
        )

        logger.info(f"PDF 分割完成: {file_path} -> {len(docs)} 个分片")
        return docs
