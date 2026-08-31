# LHB-T05 Handoff — 席位主数据、别名历史和身份假设图谱

## 1. 身份

- 任务 ID：T05
- 基线 commit：`f3075e96b565df9f8df3e4f681fc929dfedb3c77`
- 交付 commit：无
- 时间：2026-08-29

## 2. 范围

- `ab_screener/domain/seat_identity.py`
- `ab_screener/data/seat_repository.py`
- `configs/lhb_identity_policy.yaml`
- `tests/test_seat_identity.py`
- `tests/fixtures/lhb/seat_aliases.csv`

## 3. 设计

- NFKC + 空白折叠；券商法律后缀与末尾营业部层级归一，真实全称可匹配 `hm_list.orgs` 简称。
- `机构专用` → 机构通道，不细分公募/私募/QFII。
- 沪/深股通 → 互联互通聚合通道。
- `hm_list` 最高证据级 B，展示「疑似…（候选）」。
- `lookup_as_of` / `lookup_candidates_as_of` 同时按事件日有效期和 `knowledge_as_of` 可获得时点读取。
- 名称冲突不合并，进入 `queue_if_conflict`。
- candidate actor ID 由候选人物实体生成，支持一席位多候选和一候选多席位。
- precision 在保存的人工标注样本上同时给 coverage 与 mis_merge_rate。

未做 API/UI 页（T10）。lookup 已返回证据来源、置信度、有效期、冲突状态。

## 4. 测试

`tests/test_seat_identity.py` 含在全量 LHB 定向测试中。

## 5. 回滚

删除上述新增文件。

## 6. 管理者复验

- 最终判定：**返工复验通过**。
- 已关闭：candidate actor 稳定主键、多对多关系、映射内容 revision、全链 `available_at` 知识时点过滤和人工标注 precision 门禁均有反例测试。
- 说明：API／UI 证据展示归 T10 跨任务依赖，不阻断 T05 领域层验收。
- 下一步：T06 可继续；完整证据见 `docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`。
