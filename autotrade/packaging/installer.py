#!/usr/bin/env python3
"""
twquant 交易 agent 的安裝程式（A-023 的 Python 版）。`build.py --installer` 會把它連同整個程式資料夾包成一個 exe。

只裝給現在這個使用者，不需要系統管理員權限：
  - 程式放在 %LOCALAPPDATA%\\Programs\\twquant-agent（可用 --dir 換）
  - 開始功能表與桌面各一個捷徑「twquant 交易 agent」（雙擊＝照 launch.toml 開常駐的 agent）
  - 「新增或移除程式」裡看得到，可以解除安裝
已經有的 launch.toml 與 configs 裡的設定檔不會被蓋掉（重裝、更新都留著你改過的）。紀錄與資料不在安裝資料夾裡，解除安裝不會動。

  twquant-agent-setup.exe                安裝（會先問一次）
  twquant-agent-setup.exe --silent       不問、不跳視窗
  twquant-agent-setup.exe --uninstall    解除安裝
  --dir <資料夾>、--no-shortcuts、--no-register：測試用
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP = "twquant-agent"
TITLE = "twquant 交易 agent"
EXE = f"{APP}.exe"
SETUP = f"{APP}-setup.exe"
KEEP = ("launch.toml",)  # 已經有就不蓋
KEEP_DIRS = ("configs",)  # 這些資料夾裡已經有的檔不蓋
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP}"
MB_YESNO, MB_ICONQUESTION, MB_ICONINFO, MB_ICONERROR, IDYES = 0x4, 0x20, 0x40, 0x10, 6


def default_dir() -> Path:
    """預設裝在哪。"""
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP


def bundled(name: str) -> Path:
    """包在安裝程式裡的檔案。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def say(text: str, silent: bool, icon: int = MB_ICONINFO) -> None:
    """跳一個訊息視窗；--silent 就印出來。"""
    if silent:
        print(text)
    else:
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)


def ask(text: str) -> bool:
    """問一個是／否。"""
    return bool(
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, MB_YESNO | MB_ICONQUESTION) == IDYES
    )


def powershell(script: str) -> None:
    """跑一小段 PowerShell（建捷徑用）。用編碼過的指令，中文路徑才不會壞。"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True,
        capture_output=True,
    )


def shortcut_paths() -> list[Path]:
    """捷徑放哪：開始功能表與桌面。"""
    start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    return [start / f"{TITLE}.lnk", desktop / f"{TITLE}.lnk"]


def make_shortcuts(target: Path) -> None:
    """建捷徑：雙擊＝`twquant-agent.exe launch`，在安裝資料夾裡跑。"""
    for link in shortcut_paths():
        link.parent.mkdir(parents=True, exist_ok=True)
        powershell(
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
            f"$s.TargetPath = '{target / EXE}'; $s.Arguments = 'launch'; "
            f"$s.WorkingDirectory = '{target}'; $s.Description = '{TITLE}'; $s.Save()"
        )


def register(target: Path, version: str) -> None:
    """讓「新增或移除程式」看得到它（只寫這個使用者自己的登錄）。"""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (
            ("DisplayName", TITLE),
            ("DisplayVersion", version),
            ("Publisher", "twquant"),
            ("InstallLocation", str(target)),
            ("UninstallString", f'"{target / SETUP}" --uninstall'),
            ("QuietUninstallString", f'"{target / SETUP}" --uninstall --silent'),
        ):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def unregister() -> None:
    """拿掉「新增或移除程式」裡那一筆。"""
    import winreg

    with contextlib.suppress(FileNotFoundError):  # 本來就沒有登記（測試安裝）
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)


def kept(name: str, target: Path) -> bool:
    """這個檔已經在了、而且是使用者會改的那幾個：不蓋。"""
    parts = Path(name).parts
    mine = name in KEEP or (len(parts) > 1 and parts[0] in KEEP_DIRS)
    return mine and (target / name).exists()


def install(target: Path, silent: bool, shortcuts: bool, registry: bool) -> int:
    """安裝或更新。"""
    version = bundled("VERSION").read_text(encoding="utf-8").strip()
    if not silent and not ask(
        f"要把「{TITLE}」{version} 裝到這裡嗎？\n\n{target}\n\n已經有的設定檔不會被蓋掉。"
    ):
        return 1
    target.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    try:
        with zipfile.ZipFile(bundled("app.zip")) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if kept(info.filename, target):
                    skipped.append(info.filename)
                    continue
                archive.extract(info, target)
        if getattr(sys, "frozen", False):  # 自己留一份在安裝資料夾裡：解除安裝要用
            shutil.copyfile(sys.executable, target / SETUP)
    except PermissionError as exc:
        say(
            f"裝不進去：有檔案正在使用中。請先把 agent 的視窗關掉再裝一次。\n\n{exc}",
            silent,
            MB_ICONERROR,
        )
        return 2
    if shortcuts:
        make_shortcuts(target)
    if registry:
        register(target, version)
    note = f"\n\n沒有動到你原本的：{'、'.join(skipped)}" if skipped else ""
    say(
        f"裝好了：{target}{note}\n\n"
        f"第一次用請先看一下 {target / 'launch.toml'} 裡的路徑對不對，"
        f"再從開始功能表或桌面開「{TITLE}」。",
        silent,
    )
    return 0


def uninstall(target: Path, silent: bool) -> int:
    """解除安裝：拿掉捷徑、登記與程式。紀錄與資料不在這個資料夾裡，不會動。"""
    if not (target / EXE).exists():
        say(f"這裡沒有裝：{target}", silent, MB_ICONERROR)
        return 1
    if not silent and not ask(
        f"要解除安裝「{TITLE}」嗎？\n\n{target}\n\n交易的紀錄與資料不會被刪。"
    ):
        return 1
    for link in shortcut_paths():
        link.unlink(missing_ok=True)
    unregister()
    me = Path(sys.executable).resolve()
    try:
        for item in target.iterdir():
            if item.resolve() == me:
                continue  # 自己正在跑，刪不掉；下面交給另一個程序收尾
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    except PermissionError as exc:
        say(
            f"有檔案正在使用中，沒有刪完。請先把 agent 的視窗關掉再解除安裝一次。\n\n{exc}",
            silent,
            MB_ICONERROR,
        )
        return 2
    # 等這支程式結束之後，把剩下的資料夾（含這支安裝程式自己）刪掉
    # （這支程式要幾秒才會真的放開自己的檔案，所以每隔兩秒試一次，最多試十五次）
    again = (
        f'ping -n 3 127.0.0.1 >nul & rmdir /s /q "{target}" 2>nul & if not exist "{target}" exit'
    )
    subprocess.Popen(
        f"cmd /c for /l %i in (1,1,15) do @({again})",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        cwd=str(target.parent),
    )
    say("解除安裝完成。", silent)
    return 0


def main() -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dir", type=Path, help="裝在哪（預設 %%LOCALAPPDATA%%\\Programs\\twquant-agent）"
    )
    parser.add_argument("--silent", action="store_true", help="不問、不跳視窗")
    parser.add_argument("--uninstall", action="store_true", help="解除安裝")
    parser.add_argument("--no-shortcuts", action="store_true", help="不建捷徑（測試用）")
    parser.add_argument(
        "--no-register", action="store_true", help="不登記到「新增或移除程式」（測試用）"
    )
    args = parser.parse_args()
    target = (args.dir or default_dir()).resolve()
    if args.uninstall:
        return uninstall(target, args.silent)
    return install(target, args.silent, not args.no_shortcuts, not args.no_register)


if __name__ == "__main__":
    raise SystemExit(main())
