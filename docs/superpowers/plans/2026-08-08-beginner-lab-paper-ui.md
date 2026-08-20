# 策略实验室与纸面交易小白化实施记录

## 范围

- 首次默认引导模式，浏览器仅保存界面偏好；业务任务和账本状态从服务端恢复。
- Lab 固定可信验证预设、五阶段进度和三类人话结论，专业工作台完整保留。
- Paper 增加服务端下一步引导、交易日历、只读订单预览、三步历史模拟、持仓行卖出和零写入教程。
- 结构化 `ApiError` 默认展示原因与解决办法；所有真实交易能力继续关闭。

## 主要影响文件

- `paper_trading/guidance.py`
- `paper_trading/engine.py`
- `paper_trading/orders.py`
- `paper_trading/rules.py`
- `web/backend_app.py`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/components/guidance/BeginnerUi.tsx`
- `web/frontend/src/components/lab/LabGuided.tsx`
- `web/frontend/src/components/paper/PaperGuided.tsx`
- `web/frontend/src/pages/StrategyLab.tsx`
- `web/frontend/src/pages/PaperTrading.tsx`
- `web/frontend/src/layout/Topbar.tsx`

## 回滚

设置 `GUIDED_UI_ENABLED=false` 并重启后端即可回到原专业工作台。新增后端接口为只读兼容扩展，不需要删除表、回退账本或重写历史记录。
