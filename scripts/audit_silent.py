"""靜默失效的**靜態偵測**：不報錯、只安靜給出錯數字的那類缺陷。

為什麼要有這支：本週抓到五個同一形狀的缺陷，全都是靠人眼在別的任務途中撞見的——
`markets` 宣告了卻沒接上、T-139 的防護加在沒人用的那條路徑、分割與股利混成單一因子、
ETF 查無事件就 `fill_null(1.0)` 讓「含息」退化成「不含息」、`except Exception` 吞掉
簽章錯誤讓所有因子靜靜消失。靠審查抓不完，靠語言模型逐檔讀既慢又貴且不可重現。
這些形狀**大多是可以靜態判定的**，所以做成腳本：決定性、可重跑、可進閘門。

偵測六類：
  1. no-op 配置欄位——pydantic 模型宣告了，但全 repo 找不到 `cfg.<欄位>` 之類的讀取
  2. 裸 except——`except Exception` / `except:` 後面只有 pass／log，沒有 raise
  3. 危險預設填補——`fill_null` / `.get(k, 0)` 用在代表真實世界量的欄位上
  4. 重複的位移實作——同一份計算寫了多處，只有部分加了跨距防護
  5. 裸除法——在已知有 `safe_ratio` 的模組裡仍用 `/` 直除
  6. 真值判斷數值——`if x else None` 把 0 誤當「未知」

**每一條都是「值得看一眼」而非「一定是錯」**。腳本只負責把它們從 40,000 行裡撈出來，
判斷仍由人做。誤報是可接受的；漏報不是。

用法：
    python scripts/audit_silent.py                    # 全部
    python scripts/audit_silent.py --only noop-config # 只跑一類
    python scripts/audit_silent.py --out docs/reports/silent-audit.md
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "twquant"

# 這些欄位名是「代表真實世界量」的——填補它們會把缺資料偽裝成一個具體數值。
# 相對地，填補旗標或計數欄位通常無害，故不在此列。
_REAL_QUANTITY = re.compile(
    r"\b(price|close|open|high|low|volume|amount|adv|adj|value|score|ret|return|weight|"
    r"shares|equity|cash|cost|revenue|yoy|factor|ic|sharpe|cagr|mdd|turnover|"
    r"participation|slippage|commission|tax|pnl|nav|balance|capital)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Hit:
    kind: str
    file: str
    line: int
    detail: str


def _py_files(base: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(p: pathlib.Path) -> str:
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


# ── 1. no-op 配置欄位 ────────────────────────────────────────────────────
def check_noop_config() -> list[Hit]:
    """pydantic 模型宣告了欄位，但全 repo 找不到任何讀取點。

    這是 `markets` 那個缺陷的形狀：旗標看起來接上了，實際什麼都沒做，
    而**沒有任何錯誤訊息**會告訴你——配置檔照樣驗證通過。

    判定「有被讀」的方式刻意寬鬆（`.欄位`、`["欄位"]`、`欄位=`），寧可漏報也不誤報，
    因為誤報一個真的有用的欄位會讓人不信任這支腳本。
    """
    cfg = ROOT / "src" / "twquant" / "config.py"
    strat = ROOT / "src" / "twquant" / "strategy" / "config.py"
    fields: list[tuple[str, str, int]] = []  # (class, field, line)
    for path in (cfg, strat):
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    if name.startswith("_") or name == "model_config":
                        continue
                    fields.append((node.name, name, stmt.lineno))

    # 收集所有 .py 的文字，找讀取點
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in _py_files(SRC) + _py_files(ROOT / "scripts")
    )
    out: list[Hit] = []
    for klass, field, line in fields:
        used = re.search(rf"\.{re.escape(field)}\b", blob) or re.search(
            rf"""\[["']{re.escape(field)}["']\]""", blob
        )
        if not used:
            out.append(
                Hit(
                    "noop-config",
                    "src/twquant/config.py|strategy/config.py",
                    line,
                    f"{klass}.{field} 宣告了但全 repo 找不到讀取點——"
                    "配置照樣驗證通過，使用者以為設定生效了",
                )
            )
    return out


# ── 2. 裸 except ────────────────────────────────────────────────────────
def check_bare_except() -> list[Hit]:
    """`except Exception` / 裸 `except` 後面沒有 raise——黃金規則 5 明文禁止。

    `_compute_universe` 就是這樣吞掉因子函式的 TypeError，於是所有因子靜默消失、
    而該日仍回報成功。
    """
    out: list[Hit] = []
    for path in _py_files(SRC) + _py_files(ROOT / "scripts"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
            )
            if not broad:
                continue
            reraises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            if reraises:
                continue
            out.append(
                Hit(
                    "bare-except",
                    _rel(path),
                    node.lineno,
                    f"except {'Exception' if node.type else '（裸）'} 未 re-raise"
                    "——例外被吞掉後呼叫端會拿到一個「成功但不完整」的結果",
                )
            )
    return out


# ── 3. 危險預設填補 ──────────────────────────────────────────────────────
def check_default_fill() -> list[Hit]:
    """`fill_null` / `.get(k, <數值>)` 用在代表真實世界量的欄位上。

    `attach_adjustment` 的 `fill_null(1.0)` 就是這個形狀：查無除權息事件的股票
    拿到還原因子 1.0，於是「含息」靜默退化成「不含息」。
    """
    pat = re.compile(r"fill_null\(\s*([-0-9.]+)\s*\)|\.get\(\s*[^,)]+,\s*([-0-9.]+)\s*\)")
    out: list[Hit] = []
    for path in _py_files(SRC):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, ln in enumerate(text.splitlines(), 1):
            if not pat.search(ln):
                continue
            # 只在同一行或前一行提到真實世界量時才回報，降低誤報
            ctx = ln + (text.splitlines()[i - 2] if i >= 2 else "")
            if _REAL_QUANTITY.search(ctx):
                out.append(
                    Hit(
                        "default-fill",
                        _rel(path),
                        i,
                        f"對真實世界量套用預設值：{ln.strip()[:96]}"
                        "——缺資料被偽裝成一個具體數值，下游看不出差別",
                    )
                )
    return out


# ── 4. 重複的位移實作 ────────────────────────────────────────────────────
def check_unguarded_shift() -> list[Hit]:
    """`shift(...)` 而同一函式內沒有跨距防護（`_sidx` / `max_span` / `session_index`）。

    T-139 的教訓：防護加在 `forward_open_returns`，但正式配置走的是
    `cost_adjusted_return_labels`——那個函式有**自己的** shift，防護完全沒作用。
    只要 repo 裡還有第二份未防護的位移，同一個錯就會再發生一次。
    """
    guards = ("_sidx", "max_span", "session_index", "shift_within_span", "SPAN_TOLERANCE")
    out: list[Hit] = []
    for path in _py_files(SRC):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            seg = "\n".join(lines[node.lineno - 1 : (node.end_lineno or node.lineno)])
            if ".shift(" not in seg:
                continue
            if any(g in seg for g in guards):
                continue
            n = seg.count(".shift(")
            out.append(
                Hit(
                    "unguarded-shift",
                    _rel(path),
                    node.lineno,
                    f"{node.name}() 有 {n} 處列位移但無跨距防護"
                    "——個股缺 N 個開市日時，「h 日報酬」實際橫跨數月而不帶任何標記",
                )
            )
    return out


# ── 5. 裸除法 ────────────────────────────────────────────────────────────
def check_raw_division() -> list[Hit]:
    """已知有 `safe_ratio` 的模組裡仍有裸 `/`——除零得 inf，而 inf 通過每一道 null 檢查。"""
    out: list[Hit] = []
    for path in _py_files(SRC / "factors"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "safe_ratio" not in text and "pl.col" not in text:
            continue
        src_lines = text.splitlines()
        for i, ln in enumerate(src_lines, 1):
            if "pl.col" not in ln or "/" not in ln:
                continue
            if "safe_ratio" in ln or "#" in ln.split("/")[0]:
                continue
            # 已被 `pl.when(<分母> > 0).then(...)` 擋住的不算。守衛常寫在 `.then(...)`
            # 之上一兩行，故看一個小窗口而不是只看本行——只看本行會產生大量誤報，
            # 而一支充滿誤報的檢查器不會有人信。
            window = "\n".join(src_lines[max(0, i - 3) : i])
            if "pl.when(" in window and re.search(r">\s*0(\.0)?\s*\)", window):
                continue
            if re.search(r"pl\.col\([^)]*\)\s*/", ln) or re.search(r"/\s*pl\.col\(", ln):
                out.append(
                    Hit(
                        "raw-division",
                        _rel(path),
                        i,
                        f"裸除法（未用 safe_ratio）：{ln.strip()[:96]}"
                        "——分母為 0 得 inf，而 inf 不是 null，會通過 drop_nulls",
                    )
                )
    return out


# ── 6. 真值判斷數值 ──────────────────────────────────────────────────────
def check_truthy_numeric() -> list[Hit]:
    """`x if x else None` / `x or <預設>` 把數值 0 誤當成「未知」。

    引擎裡 `adv=float(adv) if adv else None` 就是這個形狀：ADV 恰為 0（整天無量）
    會被當成「ADV 未知」，於是參與率上限與衝擊滑價**一起被關掉**。
    """
    pat = re.compile(
        r"(\w+)\s*=\s*(?:float|int)\(\s*\1\s*\)\s+if\s+\1\s+else|(\w+)\s+or\s+[-0-9.]+"
    )
    out: list[Hit] = []
    for path in _py_files(SRC):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, ln in enumerate(text.splitlines(), 1):
            m = pat.search(ln)
            if m and _REAL_QUANTITY.search(ln):
                out.append(
                    Hit(
                        "truthy-numeric",
                        _rel(path),
                        i,
                        f"以真值判斷數值：{ln.strip()[:96]}"
                        "——0 是合法的量測值，被當成「未知」會靜默關掉相關約束",
                    )
                )
    return out


CHECKS = {
    "noop-config": ("no-op 配置欄位", check_noop_config),
    "bare-except": ("裸 except（吞例外）", check_bare_except),
    "default-fill": ("危險預設填補", check_default_fill),
    "unguarded-shift": ("未防護的列位移", check_unguarded_shift),
    "raw-division": ("裸除法", check_raw_division),
    "truthy-numeric": ("真值判斷數值", check_truthy_numeric),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=sorted(CHECKS), default=None)
    ap.add_argument("--out", default=None, help="另存 markdown 報告")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    names = [args.only] if args.only else list(CHECKS)
    lines = ["# 靜默失效靜態稽核", ""]
    total = 0
    for key in names:
        title, fn = CHECKS[key]
        hits = fn()
        total += len(hits)
        print(f"\n{'=' * 78}\n{key}　{title}：{len(hits)} 處\n{'=' * 78}")
        lines += [f"## {key} — {title}（{len(hits)} 處）", ""]
        for h in hits:
            print(f"  {h.file}:{h.line}\n      {h.detail}")
            lines.append(f"- `{h.file}:{h.line}` — {h.detail}")
        lines.append("")

    print(f"\n共 {total} 處待人工判讀。**每一條都是「值得看一眼」而非「一定是錯」**。")
    if args.out:
        p = pathlib.Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已寫出 {p}")


if __name__ == "__main__":
    main()
