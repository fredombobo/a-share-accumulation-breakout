# LHB-T10 Handoff — API 与仪表盘

> 只读研究 API。金额单位元。

## 1. 身份

- 任务 ID：T10
- 前置：T08
- 时间：2026-08-29

## 2. 范围

- `ab_screener/api/routers/lhb.py` + `app_factory` 注册
- `ab_screener/application/lhb_query.py`
- `web/frontend/src/api/lhb.ts`、`types/lhb.ts`
- 六页：雷达 / 画像 / 时间线 / 网络 / 质量 / 回测 Shadow
- `tests/test_lhb_api_contract.py`；`REQUIRED_V2_PATHS` 增补 9 条

未手工编辑 dist hash；由 `npm ci` + `npm run build` 生成。

## 3. 设计

- 同一接口用 `source_status` 区分 VALID_EMPTY / NOT_PUBLISHED / FETCH_FAILED / DEGRADED / COMPLETE
- 查询串未编码的 `+08:00` 还原为空格后的时区
- 身份旁展示证据级；C 级禁止确定语气
- 金额展示「元」，不乘 10000

## 4. 测试

- API 契约测试通过
- OpenAPI 最小路径通过
- `npm.cmd ci` 与 `npm.cmd run build` 通过（PowerShell 禁用 npm.ps1，改用 npm.cmd）

## 5. 回滚

卸载 router；删除前端页面与导航项。

## 6. 自评

工程可验收。无浏览器实点击（本环境无已登录的本机 :3001 会话）。
