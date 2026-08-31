# Tushare 唯一初始化方式

> 权威实现：仓库根目录 `tushare_init.py`  
> 后续所有抓取、回填、smoke **只** `from tushare_init import pro`（或 `get_pro()`）。  
> 禁止在其它文件再写 `ts.pro_api(...)`、禁止裸 `requests` 直连、禁止把 Token 写进源码。

## 调用方式（冻结）

```python
import os
import tushare as ts
pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])
pro._DataApi__http_url = 'http://a.sszhixia.cn/'
```

等价入口（项目内唯一允许的写法）：

```python
from tushare_init import pro
```

## 配置

| 项 | 位置 | 值 |
|----|------|-----|
| Token | 项目 `.env` 的 `TUSHARE_TOKEN`（已 gitignore） | 不入库 |
| HTTP URL | `TUSHARE_HTTP_URL` 或默认 | `http://a.sszhixia.cn/` |

`.env.example` 只保留占位符 `your_token_here`。

## 说明

- 底层 query 由 `tushare_init` 用 curl_cffi `impersonate=chrome` 接管，调用方式不变。
- 龙虎榜 smoke：`python scripts/lhb_tushare_smoke.py`（无 Token 则退出，不访问网络）。
- 日志与异常走 `sanitize_error()`，不得打印 Token。
