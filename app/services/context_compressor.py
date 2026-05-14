"""上下文自动压缩服务

当对话 token 使用量超过模型上下文窗口的配置比例（默认 70%）时，
自动使用大模型对较早的对话内容进行摘要压缩，用简短的摘要替代冗长的历史消息，
从而在保留关键信息的同时释放上下文窗口空间。

设计参考了 LangChain 内置的 SummarizationMiddleware，但适配了 ChatQwen
模型（因其缺少 profile 属性无法直接使用内置中间件的百分比触发模式），
并使用 dashscope 的 QwenTokenizer 实现精确的 token 计数。
"""

from typing import Any

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from loguru import logger

SUMMARY_MARKER = "[对话历史摘要]"


class ContextCompressor:
    """使用 LLM 摘要来压缩对话历史的上下文压缩器"""

    def __init__(
        self,
        model,
        context_window: int = 32768,
        trigger_fraction: float = 0.7,
        keep_recent: int = 6,
    ):
        """
        Args:
            model: 用于生成摘要的 LLM 实例（ChatQwen）
            context_window: 模型上下文窗口大小（token 数），qwen-max 为 32768
            trigger_fraction: 触发压缩的比例，默认 0.7 即 70%
            keep_recent: 压缩后保留最近的消息条数，默认 6 条（3 轮对话）
        """
        self.model = model
        self.context_window = context_window
        self.keep_recent = keep_recent
        self.trigger_tokens = int(context_window * trigger_fraction)
        self._tokenizer = None

        logger.info(
            f"上下文压缩器初始化: 窗口={context_window} tokens, "
            f"触发比例={trigger_fraction}, 触发阈值={self.trigger_tokens} tokens, "
            f"保留最近={keep_recent}条消息"
        )

    @property
    def tokenizer(self):
        """延迟加载 QwenTokenizer"""
        if self._tokenizer is None:
            try:
                from dashscope.tokenizers import get_tokenizer
                self._tokenizer = get_tokenizer("qwen-max")
                logger.info("QwenTokenizer 加载成功")
            except Exception as e:
                logger.warning(f"QwenTokenizer 加载失败: {e}, 将使用近似计数")
                self._tokenizer = None
        return self._tokenizer

    def _extract_text(self, msg: BaseMessage) -> str:
        """从消息对象中提取文本内容"""
        content = msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return " ".join(parts)
        return str(content)

    def count_tokens(self, messages: list) -> int:
        """计算消息列表的总 token 数

        优先使用 QwenTokenizer 精确计数，失败时回退到近似计数。
        """
        total = 0
        for msg in messages:
            text = self._extract_text(msg)
            if self.tokenizer is not None:
                try:
                    total += len(self.tokenizer.encode(text))
                except Exception:
                    total += len(text) // 4 + 3
            else:
                total += len(text) // 4 + 3
        return total

    def should_compress(self, messages: list) -> bool:
        """判断当前消息是否超过触发阈值"""
        return self.count_tokens(messages) > self.trigger_tokens

    def _find_existing_summary(self, messages: list) -> tuple:
        """查找消息列表中是否已有摘要

        Returns:
            (summary_text, index): 摘要文本和所在位置索引，无摘要时返回 ("", -1)
        """
        for i, msg in enumerate(messages):
            if isinstance(msg, SystemMessage) and msg.content.startswith(SUMMARY_MARKER):
                return msg.content[len(SUMMARY_MARKER):].strip(), i
        return "", -1

    async def _generate_summary(
        self,
        messages_to_summarize: list,
        existing_summary: str = "",
    ) -> str:
        """调用 LLM 生成或扩展对话摘要

        Args:
            messages_to_summarize: 需要压缩的消息列表
            existing_summary: 已有的摘要文本，非空时会在其基础上扩展

        Returns:
            生成的摘要文本
        """
        # 构建待压缩消息的文本表示
        conversation_parts = []
        for msg in messages_to_summarize:
            role = "用户" if isinstance(msg, HumanMessage) else "助手"
            text = self._extract_text(msg)
            if text.strip():
                conversation_parts.append(f"{role}: {text}")

        conversation_text = "\n".join(conversation_parts)

        # 构建摘要提示词
        if existing_summary:
            summary_instruction = (
                f"之前对话的摘要：\n{existing_summary}\n\n"
                f"请根据下面的新对话内容，更新和扩展这份摘要。"
                f"保留新旧内容中的所有重要信息，输出更新后的完整摘要。"
            )
        else:
            summary_instruction = "请对下面的对话内容进行摘要，提取所有关键信息。"

        prompt = f"""{summary_instruction}

摘要要求：
- 保留用户的重要信息（身份、需求、偏好等）
- 保留重要的决策、结论和达成的共识
- 保留未解决的问题和待办事项
- 忽略重复内容、闲聊和寒暄
- 使用简洁的中文，控制在 300 字以内

对话内容：
{conversation_text}

请直接输出摘要内容，不要添加前缀、后缀或解释："""

        response = await self.model.ainvoke(prompt)
        summary = response.content if hasattr(response, "content") else str(response)
        summary = summary.strip()

        logger.info(
            f"摘要生成完成: {len(messages_to_summarize)}条消息 -> "
            f"{len(self.tokenizer.encode(summary)) if self.tokenizer else '~'}{'tokens' if self.tokenizer else ''} 摘要"
        )
        return summary

    async def compress(self, state: dict) -> dict | None:
        """执行上下文压缩（中间件入口）

        当消息 token 数超过阈值时，将较早的消息压缩为摘要，
        保留系统提示、摘要和最近的若干条消息。

        Args:
            state: Agent 状态，包含 messages 列表

        Returns:
            包含新消息列表的字典，无需压缩时返回 None
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        if not self.should_compress(messages):
            return None

        # 分解消息结构
        first_msg = messages[0]
        to_keep = list(messages[-self.keep_recent:])

        # 检查是否已有摘要
        existing_summary, summary_idx = self._find_existing_summary(messages)

        if summary_idx > 0:
            # 有已有摘要：压缩"摘要之后、保留部分之前"的新消息
            to_compress = list(messages[summary_idx + 1:-self.keep_recent])
        else:
            # 无已有摘要：压缩"系统提示之后、保留部分之前"的所有消息
            to_compress = list(messages[1:-self.keep_recent])

        if not to_compress:
            return None

        token_before = self.count_tokens(messages)
        logger.info(
            f"触发上下文压缩: 总token={token_before}, "
            f"阈值={self.trigger_tokens}, "
            f"压缩{len(to_compress)}条消息, "
            f"保留{len(to_keep)}条消息"
        )

        try:
            new_summary = await self._generate_summary(to_compress, existing_summary)
        except Exception as e:
            logger.error(f"摘要生成失败，跳过本次压缩: {e}")
            return None

        # 构建新的消息列表
        summary_msg = SystemMessage(content=f"{SUMMARY_MARKER}\n{new_summary}")
        new_messages = [first_msg, summary_msg] + to_keep

        token_after = self.count_tokens(new_messages)
        logger.info(
            f"压缩完成: {len(messages)}条 -> {len(new_messages)}条, "
            f"token: {token_before} -> {token_after}"
        )

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
            ]
        }


def create_compression_middleware(compressor: ContextCompressor):
    """创建一个适配 LangGraph create_agent 的异步中间件函数

    返回的异步函数用作 before_model 中间件，
    在每次模型调用前检查并执行上下文压缩。

    Args:
        compressor: ContextCompressor 实例

    Returns:
        异步中间件函数，签名为 (state) -> dict | None
    """
    async def compression_middleware(state) -> dict | None:
        return await compressor.compress(state)

    return compression_middleware
