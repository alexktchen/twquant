#!/usr/bin/env python3
"""
把 autotrade 包成 Windows 的 exe（A-023 的 Python 版；用 PyInstaller，資料夾模式）。

要在裝了 pyinstaller 與 psycopg[binary] 的 Python 環境裡跑（建議另外開一個環境，不要裝進專案的環境）：
  python packaging/build.py --out C:/t/autotrade_build/dist --work C:/t/autotrade_build/work --ms-repo C:/t/Momentum_Scalping

--ms-repo：動能順勢策略會在執行時載入網友原版的 .py 檔。那些檔不在 exe 裡，但它們用到的 Python 標準模組要在，
           所以這裡掃一遍那個資料夾，把用到的標準模組一起包進去。沒給就只包 autotrade 自己用到的（strategy = none 夠用）。
做出來的是 <out>/twquant-agent/twquant-agent.exe 與旁邊一整個資料夾。
加 --installer：再把整個資料夾、configs 裡的設定檔與 launch.toml 包成單一個安裝程式 <out>/twquant-agent-setup.exe
（installer.py；只裝給目前的使用者，不用系統管理員權限）。--launch 指定要放進去的 launch.toml（放這台電腦的路徑，不進版控）。
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAME = "twquant-agent"
IMPORT = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def stdlib_used(repo: Path) -> list[str]:
    """那個資料夾的 .py 檔用到、而且這個 Python 找得到的標準模組。"""
    names: set[str] = set()
    for path in sorted(repo.glob("*.py")):
        names.update(IMPORT.findall(path.read_text(encoding="utf-8", errors="replace")))
    found = []
    for name in sorted(names & set(sys.stdlib_module_names)):
        if name != "__future__" and importlib.util.find_spec(name) is not None:
            found.append(name)
    return found


def main() -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="做出來的資料夾放哪（絕對路徑，repo 外）"
    )
    parser.add_argument(
        "--work", type=Path, required=True, help="打包的暫存資料夾（絕對路徑，repo 外）"
    )
    parser.add_argument("--ms-repo", type=Path, help="網友原版 Momentum_Scalping 的資料夾")
    parser.add_argument(
        "--installer", action="store_true", help="接著做安裝程式 twquant-agent-setup.exe"
    )
    parser.add_argument("--launch", type=Path, help="放進安裝程式的 launch.toml（沒給就放範例檔）")
    args = parser.parse_args()
    hidden = ["psycopg", "psycopg_binary", "tomllib", "zoneinfo"]
    if args.ms_repo is not None:
        if not args.ms_repo.is_dir():
            raise SystemExit(f"資料夾不存在：{args.ms_repo}")
        hidden += stdlib_used(args.ms_repo)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
    ]
    command += ["--name", NAME, "--paths", str(ROOT / "src")]
    command += [
        "--distpath",
        str(args.out),
        "--workpath",
        str(args.work),
        "--specpath",
        str(args.work),
    ]
    command += [
        "--collect-submodules",
        "autotrade",
        "--collect-all",
        "psycopg",
        "--collect-all",
        "psycopg_binary",
    ]
    command += ["--collect-all", "tzdata"]  # Windows 沒有內建的時區資料，Asia/Taipei 要靠它
    for name in sorted(set(hidden)):
        command += ["--hidden-import", name]
    command.append(str(HERE / "entry.py"))
    print("打包指令：", " ".join(command))
    done = subprocess.run(command, check=False)
    if done.returncode != 0:
        return done.returncode
    exe = args.out / NAME / f"{NAME}.exe"
    if not exe.is_file():
        print(f"打包說成功，但找不到 {exe}")
        return 1
    print(f"做好了：{exe}")
    return build_installer(args) if args.installer else 0


def build_installer(args: argparse.Namespace) -> int:
    """把程式資料夾、設定檔、啟動設定檔壓成一包，連同 installer.py 做成單一個安裝程式。"""
    app = args.out / NAME
    configs = app / "configs"
    configs.mkdir(exist_ok=True)
    for path in sorted((ROOT / "configs").glob("*.toml")):
        shutil.copyfile(path, configs / path.name)
    launch = args.launch or (HERE / "launch.example.toml")
    tomllib.loads(launch.read_text(encoding="utf-8"))  # 格式不對就在這裡停下來
    shutil.copyfile(launch, app / "launch.toml")
    stage = args.work / "installer_stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    with zipfile.ZipFile(stage / "app.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(app).as_posix())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    (stage / "VERSION").write_text(version, encoding="utf-8")
    setup = f"{NAME}-setup"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
    ]
    command += [
        "--name",
        setup,
        "--distpath",
        str(args.out),
        "--workpath",
        str(args.work / "setup"),
    ]
    command += ["--specpath", str(args.work), "--add-data", f"{stage / 'app.zip'};."]
    command += ["--add-data", f"{stage / 'VERSION'};.", str(HERE / "installer.py")]
    done = subprocess.run(command, check=False)
    if done.returncode != 0:
        return done.returncode
    out = args.out / f"{setup}.exe"
    print(f"安裝程式做好了：{out}（版本 {version}）" if out.is_file() else f"找不到 {out}")
    return 0 if out.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
