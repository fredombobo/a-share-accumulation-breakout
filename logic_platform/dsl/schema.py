"""Strategy DSL schema（docs §6.2 最小可用版）。

pydantic v2 模型，YAML/JSON 等价。三层：
  - StrategyDSL：策略声明 + params + entry + exit + position + risk
  - Condition：{feature, op, value} 条件表达式（entry 用）
  - ExitParams：出场参数（映射宿主 trade_sim fixed 模式）

错误处理：SchemaValidationError 聚合全部校验错误（字段级），
供 parser 包装为清晰中文报错。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# 支持的 op（解释器 eval 用）
SUPPORTED_OPS = {">=", "<=", ">", "<", "==", "!=", "in", "not_in", "is_nan", "not_nan"}

# 支持的 feature 命名空间（Phase 3 最小集；pred.* 预留 Phase 2 ML）
FEATURE_NAMESPACES = [
    "structure.state",
    "structure.is_breakout",
    "structure.box_high",
    "structure.box_low",
    "structure.box_mid",
    "structure.box_amp",
    "structure.box_days",
    "structure.box_quality",
    "structure.days_from_box_end",
    "structure.breakout_date",
    "vol_ma_ratio_5_20",
    "vol_percentile_60",
    "shrink_days",
    "breakout_vol_mult",
    "amount_ratio",
    "vp_corr_20",
    "ret_1",
    "ret_5",
    "ret_20",
    "atr_14",
    "dist_ma20",
    "dist_ma60",
    "dist_high_60",
    "close",
    "vol",
    # Phase 2 ML 预留（当前解释器对 pred.* 返回缺失 → 条件不通过 + warning）
    "pred.p_up_5",
    "pred.p_up_10",
    "pred.p_up_20",
]


class SchemaValidationError(ValueError):
    """DSL schema 校验失败（含字段级错误列表）。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


# 支持的 ref（动态引用，与 value 二选一；解释器从特征面板取值）
SUPPORTED_REFS = ["box_mid", "box_high", "box_low", "ma5", "ma10", "ma20"]


class Condition(BaseModel):
    """条件表达式：{feature, op, value|ref}。is_nan/not_nan 无需 value。"""

    feature: str = Field(description="特征名，见 FEATURE_NAMESPACES")
    op: str = Field(description="操作符，见 SUPPORTED_OPS")
    value: float | int | str | list | None = Field(
        default=None, description="比较目标（is_nan/not_nan 可省略）"
    )
    ref: str | None = Field(
        default=None, description="动态引用（box_mid/box_high/box_low/ma5/ma10/ma20），与 value 二选一"
    )

    @model_validator(mode="after")
    def _check(self) -> Condition:
        problems: list[str] = []
        if self.feature not in FEATURE_NAMESPACES:
            problems.append(
                f"entry.feature '{self.feature}' 不支持（可用: "
                f"{', '.join(FEATURE_NAMESPACES)}）"
            )
        if self.op not in SUPPORTED_OPS:
            problems.append(
                f"entry.op '{self.op}' 不支持（可用: {', '.join(sorted(SUPPORTED_OPS))}）"
            )
        if self.op in ("in", "not_in") and not isinstance(self.value, list):
            problems.append(f"op '{self.op}' 要求 value 为列表")
        if self.op in ("is_nan", "not_nan"):
            if self.value is not None or self.ref is not None:
                problems.append(f"op '{self.op}' 不应提供 value/ref")
        else:
            if self.value is None and self.ref is None:
                problems.append(f"op '{self.op}' 要求提供 value 或 ref")
            if self.ref is not None and self.ref not in SUPPORTED_REFS:
                problems.append(f"ref '{self.ref}' 不支持（可用: {', '.join(SUPPORTED_REFS)}）")
        if problems:
            raise SchemaValidationError(problems)
        return self


class EntryRule(BaseModel):
    """入场规则：all（全部满足）+ any（任一满足），两组独立。"""

    all: list[Condition] = Field(default_factory=list)
    any: list[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> EntryRule:
        if not self.all and not self.any:
            raise SchemaValidationError(["entry 必须至少包含 all 或 any 一个条件"])
        return self


class ExitParams(BaseModel):
    """出场参数（映射宿主 trade_sim fixed 模式）。"""

    stop_pct: float = Field(default=0.07, ge=0, lt=1, description="止损比例")
    target_pct: float = Field(default=0.12, ge=0, lt=1, description="止盈比例")
    max_hold: int = Field(default=15, ge=1, le=120, description="最长持有交易日")


class PositionParams(BaseModel):
    """仓位（MVP 仅记录；组合资金回测在 Phase 4+ 接入）。"""

    method: str = Field(default="fixed_pct", pattern="^(fixed_pct|vol_target)$")
    max_pct: float = Field(default=0.10, ge=0, le=1)
    max_names: int = Field(default=15, ge=1, le=100)


class RiskParams(BaseModel):
    """风控（Phase 3 最小：st 规避标记；regime 联动 Phase 4）。"""

    avoid_st: bool = Field(default=True)
    regime_block: bool = Field(default=False)


class BacktestParams(BaseModel):
    """回测区间/采样参数（模板可覆盖，CLI 可 --set 覆盖）。"""

    start: str = Field(default="20250101", pattern=r"^\d{8}$")
    end: str = Field(default="20260731", pattern=r"^\d{8}$")
    step: int = Field(default=5, ge=1, le=60, description="采样日步长（交易日）")
    max_codes: int = Field(default=200, ge=10, le=5000)
    lookback_bars: int = Field(default=180, ge=120, le=400)
    workers: int = Field(default=4, ge=1, le=16)


class StrategyDSL(BaseModel):
    """完整策略 DSL（docs §6.2 最小可用版）。"""

    strategy: dict = Field(description="声明：id/version/name/research_only")
    params: BacktestParams = Field(default_factory=BacktestParams)
    entry: EntryRule
    exit: ExitParams = Field(default_factory=ExitParams)
    position: PositionParams = Field(default_factory=PositionParams)
    risk: RiskParams = Field(default_factory=RiskParams)

    @model_validator(mode="after")
    def _check(self) -> StrategyDSL:
        sid = self.strategy.get("id")
        if not sid or not str(sid).strip():
            raise SchemaValidationError(["strategy.id 必填"])
        if not self.strategy.get("name"):
            raise SchemaValidationError(["strategy.name 必填"])
        return self

    @property
    def id(self) -> str:
        return str(self.strategy["id"])

    @property
    def version(self) -> str:
        return str(self.strategy.get("version", "0.0.0"))

    @property
    def name(self) -> str:
        return str(self.strategy.get("name", self.id))

    @property
    def research_only(self) -> bool:
        return bool(self.strategy.get("research_only", True))

    @property
    def dsl_yaml(self) -> str:
        """序列化回 YAML（入库用）。"""
        import yaml

        return yaml.safe_dump(self.model_dump(), allow_unicode=True, sort_keys=False)


def validate_strategy(data: dict) -> StrategyDSL:
    """校验 dict → StrategyDSL；失败抛 SchemaValidationError（字段级中文）。"""
    try:
        return StrategyDSL.model_validate(data)
    except SchemaValidationError:
        raise
    except Exception as exc:  # pydantic 内部错误 → 展开字段错误
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            errs = []
            for e in exc.errors():
                loc = ".".join(str(x) for x in e["loc"])
                errs.append(f"{loc}: {e['msg']}")
            raise SchemaValidationError(errs) from exc
        raise SchemaValidationError([str(exc)]) from exc
