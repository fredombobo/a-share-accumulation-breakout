# AI 解读模型配置

模型客户端统一入口：`ab_screener/ai/client.py`。
Tushare Token 用于行情数据，不能当作模型 API Key。

在项目根目录的本地 `.env` 中填写一种模型配置。该文件不纳入 Git；不要把真实密钥写入源码或前端。

## DeepSeek

```dotenv
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=填写实际密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

## OpenAI 或兼容接口

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=填写该服务的实际密钥
OPENAI_BASE_URL=填写服务提供的API根地址
OPENAI_MODEL=填写该服务支持的模型标识
```

根地址应指向 OpenAI 兼容 API，例如服务给出的 `/v1` 根路径，不要重复填写 `/chat/completions`。本项目会在根地址后追加该路径。

## 本地 Ollama

```dotenv
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=填写本机已安装的模型标识
```

本地模型需要已安装并启动对应服务；填写模型名本身不会安装、启动或验证模型。

## 使用与状态

1. 保存 `.env` 后，在个股页「AI 证据评测」点击「刷新模型状态」。配置在下一次读取时生效；非空显式进程环境变量优先于文件配置。
2. 「已配置」表示配置字段已读取，尚不证明服务可达、密钥有效或模型有权限。
3. 点击「生成 AI 文字解读」才会发起模型请求。请求内容为该股的本地行情、形态、资金及财务摘要；GET 查看与刷新状态不会调用模型。
4. 认证、超时、限流及无效响应分别反馈。成功后的解读注明实际提供方、模型及生成时间。
5. 8001 的本次解读仅在当前页面展示，不创建生产库缓存表，也不修改选股结果或交易状态。

如果未指定 `AI_PROVIDER`，系统按 DeepSeek、OpenAI 兼容接口、本地 Ollama 顺序选择已配置项；全部未配置时保留明确的缺配置状态。未知提供方不自动转发到另一家服务。
