"""DSL schema/parser 测试：校验与错误处理。"""
from __future__ import annotations

import pytest

from logic_platform.dsl.parser import DslParseError, load_template, parse_text
from logic_platform.dsl.schema import (
    Condition,
    SchemaValidationError,
)

VALID_YAML = """
strategy:
  id: t1
  version: "1.0.0"
  name: 测试策略
  research_only: true
params:
  start: "20250101"
  end: "20260731"
entry:
  all:
    - { feature: "structure.state", op: "in", value: ["BREAKOUT"] }
    - { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }
  any: []
exit:
  stop_pct: 0.07
  target_pct: 0.12
  max_hold: 15
"""


def test_parse_valid_yaml():
    dsl = parse_text(VALID_YAML)
    assert dsl.id == "t1"
    assert dsl.entry.all[0].feature == "structure.state"
    assert dsl.exit.max_hold == 15
    assert dsl.research_only is True


def test_bad_yaml_raises_with_line():
    with pytest.raises(DslParseError) as ei:
        parse_text("strategy:\n  id: [broken\n  entry: {}")
    assert "语法错误" in str(ei.value)


def test_unknown_feature_rejected():
    with pytest.raises(SchemaValidationError) as ei:
        parse_text(VALID_YAML.replace("structure.state", "not_a_feature"))
    assert "not_a_feature" in str(ei.value)
    assert "不支持" in str(ei.value)


def test_unknown_op_rejected():
    with pytest.raises(SchemaValidationError):
        parse_text(VALID_YAML.replace('op: "in"', 'op: "bogus"'))


def test_value_or_ref_required():
    with pytest.raises(SchemaValidationError):
        parse_text(VALID_YAML.replace('value: ["BREAKOUT"]', ""))


def test_ref_supported():
    dsl = parse_text(VALID_YAML.replace(
        '- { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }',
        '- { feature: "close", op: ">=", ref: "box_mid" }'))
    assert dsl.entry.all[1].ref == "box_mid"


def test_bad_ref_rejected():
    with pytest.raises(SchemaValidationError):
        parse_text(VALID_YAML.replace(
            '- { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }',
            '- { feature: "close", op: ">=", ref: "nope" }'))


def test_in_requires_list_value():
    with pytest.raises(SchemaValidationError):
        parse_text(VALID_YAML.replace('value: ["BREAKOUT"]', "value: 1"))


def test_empty_entry_rejected():
    with pytest.raises(SchemaValidationError):
        parse_text(VALID_YAML.replace(
            "  all:\n    - { feature: \"structure.state\", op: \"in\", value: [\"BREAKOUT\"] }\n    - { feature: \"vol_ma_ratio_5_20\", op: \">=\", value: 1.6 }\n  any: []",
            "  all: []\n  any: []"))


def test_load_template_builtin():
    dsl = load_template("vol_breakout_v1")
    assert dsl.id == "vol_breakout_v1"


def test_load_missing_template_raises():
    with pytest.raises(FileNotFoundError):
        load_template("no_such_template")


def test_condition_is_nan_no_value():
    c = Condition(feature="close", op="is_nan")
    assert c.op == "is_nan"


def test_dsl_yaml_roundtrip():
    dsl = parse_text(VALID_YAML)
    dsl2 = parse_text(dsl.dsl_yaml)
    assert dsl2.id == dsl.id
    assert dsl2.exit.stop_pct == dsl.exit.stop_pct


def test_strategy_requires_id():
    with pytest.raises(SchemaValidationError):
        parse_text(VALID_YAML.replace('id: t1', 'id: ""'))
