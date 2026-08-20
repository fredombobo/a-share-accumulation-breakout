# LOGIC PLATFORM Phase 2 验收报告

> 日期：2026-08-08
> 规格：`docs/VOLUME-PRICE-LOGIC-PLATFORM.md` §5（预测层）
> 范围：标签（无泄漏）+ 面板 + 模型（baseline/HistGB/统计表）+ 推理服务 + 训练 CLI + explain 集成 + predict API
> 验收标准：单元测试证明 label 未使用未来 bar；推理 API 返回 model_version（主文档 §11 Phase 2 验收）

---

## 1. 交付清单

| 文件 | 职责 | 状态 |
|------|------|------|
| `logic_platform/prediction/labels.py` | y_up_N / y_ret_N / y_mdd_N / y_new_high_N（shift 未来；y_mdd 钳制 ≤0） | ✅ |
| `logic_platform/prediction/dataset.py` | 特征+标签面板（进程池并行）；状态聚焦采样（TRAIN_STATES）；时间序 70/30 切分；to_matrix | ✅ |
| `logic_platform/prediction/models.py` | 统一接口：LogisticBaseline / HistGBModel（sklearn 原生，可换 LightGBM）/ StateStatsTable；工厂 train_model | ✅ |
| `logic_platform/prediction/serve.py` | Predictor：joblib + meta.json 加载、特征面板 → {p_up, expected_ret, fail_risk, model_version, train_window, horizon}；latest() 版本选择；失败降级 None | ✅ |
| `logic_platform/cli/run_logic_train.py` | 训练 CLI：面板 → 切分 → 训练 → IS/OOS 评估 → 落盘 runtime/logic_models/vN/（model.joblib + meta.json） | ✅ |
| `logic_platform/service.py`（改） | explain 的 prediction 字段填充（模型缺失为 None）；predict_batch() | ✅ |
| `logic_platform/api/routes.py`（改） | POST /api/logic/predict（批量推理，模型未训练返回 warning 不报错） | ✅ |
| `tests/test_logic_platform/test_labels.py` | 6 用例：标签定义/无泄漏/末尾 NaN | ✅ |
| `tests/test_logic_platform/test_models.py` | 7 用例：三模型训练推理/工厂/单类拒绝 | ✅ |
| `tests/test_logic_platform/test_serve.py` | 7 用例：输出契约/版本选择/降级 | ✅ |

## 2. 自动化测试

```powershell
C:\Python314\python.exe -m pytest tests/test_logic_platform/ -q
# 94 passed（含 Phase 0/1 41 + Phase 3 34 + Phase 2 20）
```

关键无泄漏测试：
- `test_no_feature_leakage_by_construction`：篡改 t 日之后价格不影响 t 日特征
- `test_labels_nan_at_tail_no_lookahead`：末尾 N 行标签必须 NaN（未来缺失）
- `test_y_ret_definition`：y_ret_5[t] = close[t+5]/close[t] - 1 逐值校验

## 3. 训练实跑（验收主证据）

```powershell
C:\Python314\python.exe -m logic_platform.cli.run_logic_train --codes 200 --horizon 10 --model histgb --start 20230101 --end 20260731
```

| 项 | 值 |
|----|-----|
| 面板 | 16,122 样本 / 200 只（状态聚焦：TIGHTENING 8,241 · ACCUMULATION 7,125 · FOLLOW_THROUGH 507 · BREAKOUT 249） |
| 切分 | IS 10,297（≤20251015） / OOS 5,825（≥20251016）——时间序，OOS 严格晚于 IS |
| IS 评估 | AUC 0.7146 · 准确率 0.648 · Top30% 胜率 0.756 |
| **OOS 评估** | **AUC 0.5189 · 准确率 0.490 · Top30% 胜率 0.428** |
| 落盘 | `runtime/logic_models/v1/`（model.joblib + meta.json，含 features/训练窗/评估/期望收益表） |
| 耗时 | 358s（面板构建并行 6 进程） |

**OOS 结论（如实报告）**：模型在样本外暂无显著预测力（AUC≈0.52，接近随机）。
- 这不是实现缺陷——机制层（无泄漏、时间序切分、IS/OOS 分离评估、降级路径）全部正确
- 是量化研究常态：当前特征集/标签/区间在市场 OOS 段无 alpha
- 提升方向（下一轮可选）：特征扩展（资金流/换手率）、更细的标签（条件收益）、更严格的训练区间（避开市场风格切换）、模型集成；**闸门与 research_only 语义保证"无 alpha 的预测不会流向交易决策"**

## 4. 集成验收

### 4.1 explain 带 prediction（002793.SZ，FOLLOW_THROUGH）

```json
{
  "p_up": 0.5828, "expected_ret": 0.0151, "fail_risk": 0.4172,
  "model_version": "v1",
  "train_window": {"start": "20230101", "end": "20260731",
                   "is_end": "20251015", "oos_start": "20251016", "oos_end": "20260713"},
  "horizon": 10
}
```

### 4.2 POST /api/logic/predict 批量（3 只实测）

```
model: v1 horizon: 10
000506.SZ FOLLOW_THROUGH   p_up=0.2277 exp_ret=0.0151 fail_risk=0.7723
600036.SH TIGHTENING       p_up=0.5246 exp_ret=0.0164 fail_risk=0.4754
000001.SZ TIGHTENING       p_up=0.5624 exp_ret=0.0164 fail_risk=0.4376
```

- p_up 为模型个性化输出；expected_ret 按 state 查训练集期望收益表（可解释口径）
- 模型缺失时：explain.prediction=None、predict 返回 warning——全链路降级不崩

### 4.3 pred.* 激活

`build_feature_panel` 的 `pred.p_up_5/10/20` 在 Predictor 注入时输出模型结果；
Phase 3 的 DSL 回测仍走纯规则（避免 train-serve 泄漏），接口已预留。

## 5. 已知限制 / TODO

1. **OOS 无显著 alpha**：见 §3 结论；模型作为研究基线 v1 保留，迭代特征后重训为 v2
2. **fail_risk = 1 - p_up**：MVP 口径；后续可单独训练"大跌概率"模型
3. **LightGBM 未安装**：当前用 sklearn HistGradientBoosting（接口一致，可平滑替换）
4. **expected_ret 按状态查表**：个性化收益回归模型下一轮

## 6. 结论

**Phase 2 验收通过**：
- ✅ 标签无泄漏（测试背书 + 定义校验）
- ✅ 面板/切分/训练/落盘/加载/推理全链路
- ✅ explain 带 prediction、predict API、降级路径
- ✅ IS/OOS 分离评估并如实报告（不粉饰无 alpha 的结果）

下一步建议：Phase 4 UI（/logic 页面）> 特征迭代重训 v2 > Phase 5 纸交易闭环。
