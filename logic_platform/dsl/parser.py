"""DSL 解析器：YAML/JSON → StrategyDSL。

错误处理分层：
  - DslParseError     ：YAML 语法错误（含行号）
  - SchemaValidationError：schema 校验失败（字段级，来自 schema.py）
  - FileNotFoundError 透传（模板缺失）
"""
from __future__ import annotations

from pathlib import Path

import yaml

from logic_platform.dsl.schema import StrategyDSL, validate_strategy


class DslParseError(ValueError):
    """YAML 语法错误。"""

    def __init__(self, path: str, msg: str, line: int | None = None):
        self.path = path
        self.line = line
        loc = f"（第 {line} 行）" if line else ""
        super().__init__(f"DSL 语法错误 {path}{loc}: {msg}")


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def load_template(name: str) -> StrategyDSL:
    """加载内置模板：name 可含 .yaml 后缀或纯 id。"""
    path = Path(name)
    if not path.is_absolute() and path.suffix != ".yaml":
        path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"模板不存在: {path}（可用: {list_templates()}）")
    return load_file(path)


def load_file(path: str | Path) -> StrategyDSL:
    """解析 YAML 文件 → StrategyDSL。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"无法读取模板文件: {path}: {exc}") from exc
    return parse_text(text, str(path))


def parse_text(text: str, source: str = "<string>") -> StrategyDSL:
    """解析 YAML 文本 → StrategyDSL。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        line = getattr(exc, "problem_mark", None)
        raise DslParseError(source, str(exc), line.line + 1 if line else None) from exc
    if not isinstance(data, dict):
        raise DslParseError(source, "顶层必须是映射（strategy: ...）")
    return validate_strategy(data)


def list_templates() -> list[str]:
    """内置模板 id 列表。"""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml"))
