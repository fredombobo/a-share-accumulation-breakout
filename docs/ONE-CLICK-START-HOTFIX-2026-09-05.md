# 一键启动热修复计划与验收

用户日志暴露本次本地验收遗漏：双击路径误选 C:\Python314，旧 mplfinance 依赖无法安装，批处理出现截断的 gent 命令。

1. 新增标准库运行时选择器 `launcher_runtime.py`；`easy_start.py`、`bootstrap.py`、开发启动器共用。优先项目 `.venv312`，首次仅用实测 Python 3.12 创建环境，不覆盖损坏的环境，不向系统 Python 安装。
2. 依赖显式固定 `mplfinance==0.12.10b0`。先用本地元数据检查；缺包时显示安装日志并应用现有锁文件，最后 pip check，不把全部失败说成网络错误。
3. `一键启动.bat` 改纯 ASCII，Git 固定 CRLF；中文提示在 Python 层。保留退出码、参数、含空格目录以及失败反馈。
4. 补离线单元测试和 Windows 真批处理成功/失败契约；运行严格质量门、验证已满足依赖无需安装，再通过用户同一批处理路径启动并验证 AB/8001 身份。
5. 更新小白手册和 README；保留策略 FAIL 与原账本，不涉及 AETF/8000。实际验收结果在完成后补入本文件。

官方依据：[mplfinance 0.12.10b0](https://pypi.org/project/mplfinance/0.12.10b0/) 为预发行版本；[pip 预发行规则](https://pip.pypa.io/en/stable/cli/pip_install/#pre-release-versions) 支持显式指定该版本，不应为所有依赖开启预发行升级。

## 已取得的验收证据

- Python 3.14 实际导入两个入口并调用运行时选择：均返回 `E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe`（3.12.10）。
- 本地依赖检查显示“无需联网重装”，`pip check` 无冲突；`--dry-run --ignore-installed --no-deps mplfinance==0.12.10b0` 可解析目标包，不使用全局 `--pre`。
- 启动器新增 20 项测试，连同既有端口隔离 3 项，23/23 通过；含真实 Windows `cmd.exe`、路径带空格及 &、成功与失败退出码、参数转发、不阻塞等待后台服务。
- 真正执行 `一键启动.bat --skip-sync --no-browser`，退出码 0；8001 身份检查 `IDENTITY_OK accumulation_breakout`，构建 `e25e0af25c92`。此验收不执行行情同步、扫描或回测，不把启动成功等同数据新鲜度通过。
- GitHub 增加独立 Windows 启动器契约任务，避免仅在 Linux 或仅经手工指定 Python 的路径验收。
- 完整本地严格质量门通过：1,171 项 Python（272.06 秒）、21 项前端、Ruff、Mypy（33 个文件）、严格架构与生产构建。原 ECharts 体积 warning 保留，不属于这次启动热修复。
- 完成构建后再次验证运行中的 AB/8001，仍匹配 `e25e0af25c92`，LIVE=false。没有用 3.14 冒充实际服务运行时。
- Windows 启动器独立 CI 使用 `--noconftest`，只依赖 pytest/packaging，不加载真实行情库或供应商；Linux 主套件仍检查全部通用逻辑，实际 cmd.exe 的两项测试由 Windows 任务负责。
- 云端以本次提交的 Actions 结果为准，本地通过不冒充尚未返回的云端结果。

## 回滚与使用

本机已恢复启动；用户仍双击原 `一键启动.bat`，不必重填 Token 或卸载系统 Python 3.14。若需要回滚，正常 revert 此次代码提交并重启 8001；不要删除 `.env`、行情库、研究报告或旧账本。本次没有改变交易参数、研究门槛、数据库 schema 或券商能力。
