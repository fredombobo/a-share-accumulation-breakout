# Tushare 唯一初始化方式

> 权威实现：仓库根目录 `tushare_init.py`  
> 后续所有抓取、回填、smoke **只** `from tushare_init import pro`（或 `get_pro()`）。  
> 禁止在其它文件再写 `ts.pro_api(...)`、禁止裸 `requests` 直连、禁止把 Token 写进源码。

## 调用方式（用户于 2026-09-14 再确认）

```python
import os
import tushare as ts
pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])
pro._DataApi__http_url = 'http://a.sszhixia.cn/'
```

等价入口（项目内唯一允许的写法）：

```python
from tushare_init import pro

# 在需要函数形式时，返回同一个初始化客户端：
from tushare_init import get_pro
pro = get_pro()
```

## 配置

| 项 | 位置 | 值 |
|----|------|-----|
| Token | 项目 `.env` 的 `TUSHARE_TOKEN`（已 gitignore） | 不入库 |
| HTTP URL | `TUSHARE_HTTP_URL` 或默认 | `http://a.sszhixia.cn/` |

`.env.example` 只保留占位符 `your_token_here`。

本地 `.env` 已保存本次用户提供的 Token 与 `TUSHARE_HTTP_URL=http://a.sszhixia.cn/`。
初始化时读取项目 `.env`，覆盖父进程遗留的同名配置。不要在其它模块复制 Token 或另建客户端。
修改配置后，已有常驻进程需要重启才能使先前导入的客户端生效。

## 说明

- 底层 query 由 `tushare_init` 用 curl_cffi `impersonate=chrome` 接管，调用方式不变。
- HTTP 仅允许本次指定的根地址；其它节点须为 HTTPS，仍校验证书并拒绝重定向。
- 地址使用 HTTP，传输不经过 TLS；不将其标记为 HTTPS / TLS 验证通过。
- 龙虎榜 smoke：`python scripts/lhb_tushare_smoke.py`（无 Token 则退出，不访问网络）。
- 日志与异常走 `sanitize_error()`，不得打印 Token。
