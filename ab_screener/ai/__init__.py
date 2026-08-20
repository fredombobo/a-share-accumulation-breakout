"""AI 解读层（移植自 astock 开源项目 backend/ai，适配本地依赖）。

- client.py  : DeepSeek / OpenAI / Ollama 统一客户端（requests 实现，接口对齐 astock）
- prompts.py : 五维评分 + 买卖建议 + 目标价/止损 提示词模板

设计原则：函数签名与 astock 保持一致，便于未来双向合并；
仅依赖 requests（项目 requirements 已有），不引入 httpx。
"""
