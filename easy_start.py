"""
小白一键启动
============
双击「一键启动.bat」或运行：
  python easy_start.py

自动完成：
  1) 找 Python
  2) 检查/创建 .env（引导填写 Token）
  3) 安装依赖
  4) 增量同步行情（库空则首次多拉一些）
  5) 启动 Web（单端口 :8000，自带前端）
  6) 打开浏览器

停止：双击「停止.bat」或 Ctrl+C 后关窗口
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
REQ = ROOT / "requirements.txt"
DIST = ROOT / "web" / "frontend" / "dist" / "index.html"
RUNTIME = ROOT / "runtime"
DB = RUNTIME / "stock_data.db"


def _banner(msg: str) -> None:
    print()
    print("=" * 52)
    print(msg)
    print("=" * 52)


def _clear_proxy() -> None:
    os.environ.pop("PYTHONPATH", None)
    for k in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        os.environ.pop(k, None)


def _find_python() -> str:
    for p in (
        Path(r"C:\Python314\python.exe"),
        Path(r"C:\Python313\python.exe"),
        Path(r"C:\Python312\python.exe"),
    ):
        if p.is_file():
            return str(p)
    return sys.executable or "python"


def _read_token() -> str:
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if token and token not in ("your_token_here", "changeme"):
        return token
    if not ENV_PATH.is_file():
        return ""
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("TUSHARE_TOKEN=") and not s.startswith("#"):
                v = s.split("=", 1)[1].strip().strip('"').strip("'")
                if v and v not in ("your_token_here", "changeme"):
                    return v
    except OSError:
        pass
    return ""


def _ensure_env(interactive: bool) -> bool:
    """确保 .env 存在且有 Token。返回是否可用。"""
    if not ENV_PATH.is_file():
        if ENV_EXAMPLE.is_file():
            shutil.copy(ENV_EXAMPLE, ENV_PATH)
            print(f"[配置] 已创建 {ENV_PATH.name}（从 .env.example 复制）")
        else:
            ENV_PATH.write_text(
                "TUSHARE_TOKEN=your_token_here\n"
                "TUSHARE_HTTP_URL=http://a.sszhixia.cn/\n",
                encoding="utf-8",
            )
            print(f"[配置] 已创建 {ENV_PATH.name}")

    token = _read_token()
    if token:
        print("[配置] Token 已就绪")
        os.environ["TUSHARE_TOKEN"] = token
        return True

    print()
    print("还没有填写 Tushare Token，系统无法拉行情。")
    print("获取方式：tushare.pro 注册 → 个人中心复制 token")
    print(f"也可以稍后手动编辑文件：{ENV_PATH}")
    print()
    if not interactive:
        print("[配置] 非交互模式：请编辑 .env 后重新运行")
        return False

    try:
        entered = input("请粘贴 Token 后回车（直接回车=稍后手动填）: ").strip()
    except EOFError:
        entered = ""
    if not entered:
        print("[配置] 已跳过。请编辑 .env 里的 TUSHARE_TOKEN= 后再双击启动。")
        if sys.platform.startswith("win"):
            try:
                os.startfile(str(ENV_PATH))  # type: ignore[attr-defined]
            except OSError:
                pass
        return False

    # 写回 .env
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("TUSHARE_TOKEN="):
            out.append(f"TUSHARE_TOKEN={entered}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.insert(0, f"TUSHARE_TOKEN={entered}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ["TUSHARE_TOKEN"] = entered
    print("[配置] Token 已保存到 .env")
    return True


def _pip_install(py: str) -> None:
    print("[依赖] 检查 Python 包…")
    cmd = [py, "-m", "pip", "install", "-r", str(REQ), "-q"]
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print("[依赖] pip 安装失败，请检查网络后重试")
        sys.exit(1)
    print("[依赖] OK")


def _load_dotenv() -> None:
    if not ENV_PATH.is_file():
        return
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def _sync_if_needed(py: str, force_sync: bool) -> None:
    first = not DB.is_file() or DB.stat().st_size < 1_000_000
    if first:
        print("[数据] 首次建库：大约 30～90 分钟（视网络），请不要关窗口…")
        days = 200
    else:
        print("[数据] 增量同步：大约 2～10 分钟…")
        days = 30
    if not force_sync and not first:
        # 有库也默认同步，保证新鲜
        pass
    cmd = [py, str(ROOT / "sync_daily.py"), "--days", str(days)]
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print("[数据] 同步失败。可先打开界面查看旧数据，或检查 Token/网络后重试。")
    else:
        print("[数据] 同步完成")


def _start_server(py: str) -> subprocess.Popen:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    out_log = RUNTIME / "easy_backend.out.log"
    err_log = RUNTIME / "easy_backend.err.log"
    backend = ROOT / "web" / "backend_app.py"
    print("[启动] Web 服务 http://127.0.0.1:8000/ …")
    # 前台模式更易 Ctrl+C；小白双击用新窗口
    if os.environ.get("EASY_START_FOREGROUND") == "1":
        # 直接 exec 风格
        os.chdir(ROOT / "web")
        os.execv(py, [py, str(backend)])  # noqa: S606
    fout = open(out_log, "w", encoding="utf-8")  # noqa: SIM115
    ferr = open(err_log, "w", encoding="utf-8")  # noqa: SIM115
    p = subprocess.Popen(
        [py, str(backend)],
        cwd=str(ROOT / "web"),
        stdout=fout,
        stderr=ferr,
        env=os.environ.copy(),
    )
    (RUNTIME / "backend.pid").write_text(str(p.pid), encoding="ascii")
    return p


def _port_in_use(port: int = 8000) -> bool:
    """检查端口是否已被占用（存在监听者）。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False


def _wait_health(timeout: float = 40.0) -> bool:
    import urllib.request

    url = "http://127.0.0.1:8000/api/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    """兼容入口：有 --token/--yes 时转交 bootstrap（Agent 友好）。"""
    argv = list(argv or sys.argv[1:])
    # Agent / 非交互：统一走 bootstrap.py
    if any(a.startswith("--token") or a in ("--yes", "-y") for a in argv) or os.environ.get("TUSHARE_TOKEN"):
        if "--yes" not in argv and "-y" not in argv and not sys.stdin.isatty():
            argv = ["--yes", *argv]
        from bootstrap import main as bootstrap_main
        return bootstrap_main(argv)

    skip_sync = "--no-sync" in argv or "--skip-sync" in argv
    no_browser = "--no-browser" in argv
    interactive = "--yes" not in argv and sys.stdin.isatty()

    _clear_proxy()
    _banner("横盘吸筹选股 · 小白一键启动")
    print("目录:", ROOT)
    print("提示: Agent 请用  python bootstrap.py --token <TOKEN> --yes")

    if not DIST.is_file():
        print("[警告] 未找到前端打包文件 web/frontend/dist")

    py = _find_python()
    print("[环境] Python =", py)

    _load_dotenv()
    ok_token = _ensure_env(interactive=interactive)
    _pip_install(py)
    _load_dotenv()

    if ok_token and not skip_sync:
        try:
            _sync_if_needed(py, force_sync=False)
        except KeyboardInterrupt:
            print("\n[数据] 同步被中断，继续启动界面…")
    elif not ok_token:
        print("[数据] 无 Token，跳过同步（可先看界面说明）")

    if _wait_health(timeout=4.0):
        print("[启动] 服务已在运行")
        if not no_browser:
            webbrowser.open("http://127.0.0.1:8000/")
        print("打开: http://127.0.0.1:8000/")
        return 0

    proc = _start_server(py)
    if not _wait_health(timeout=45):
        print("[错误] 服务未就绪，请查看 runtime/easy_backend.err.log")
        if _port_in_use(8000):
            print()
            print("[提示] 端口 8000 已被其他进程占用（可能是上次未正确关闭的残留服务）。")
            print("       请先双击「停止.bat」清理残留进程，再重新启动。")
            print("       若仍无法停止，可在任务管理器结束名为 python.exe 的残留进程。")
        return 1

    print()
    print("✓ 已启动  http://127.0.0.1:8000/")
    print("  点「扫描」→ 等 5～15 分钟 → 看 A 池")
    if not no_browser:
        webbrowser.open("http://127.0.0.1:8000/")

    if interactive and sys.platform.startswith("win"):
        print("服务在后台运行中。按回车仅关闭本窗口…")
        try:
            input()
        except EOFError:
            pass
    else:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
