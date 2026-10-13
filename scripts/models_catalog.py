"""模型清冊：data/models 底下每個訓練過的模型（含封存、作廢）的用途、標籤、特徵數、訓練期與樣本外成績。

讀每個模型的 meta.json（訓練時寫的，不靠記憶），依代號前綴分系列，系列說明寫在 FAMILIES。輸出 markdown：

    uv run --all-groups python scripts/models_catalog.py --models D:/project/twquant/data/models \\
        --out <repo 絕對路徑>/docs/reports/models-catalog-2026-10-13.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

# 正式上線（每日打分）的模型與用途；改 configs/ml.yaml 時這裡要跟著改
LIVE = {
    "lgbm_lw_net_h20_20260225_83890036": "上線：大盤精選的排序模型",
    "lgbm_quietA20_tu_h20_20260430_655df6d7": "上線：潛伏飆股、高分爆量",
    "lgbm_lw_dd_h60_20260225_1bd7c6a0": "每日打分（參考，兩條線都沒用）",
}

# (代號前綴, 系列名稱, 白話說明)；依序比對，先比到的算
FAMILIES: list[tuple[str, str, str]] = [
    (
        "lgbm_h10_",
        "第一代：10 日報酬排名",
        "2026-09 初的第一批模型（D-038～D-051）：預測未來 10 個交易日報酬的全市場排名，25～30 項價量、籌碼、基本面特徵，"
        "2020 年起的資料。樣本外 Rank IC 0.08～0.09 看起來不錯，但做成每週換股的策略扣成本後幾乎沒賺——"
        "這種標籤偏好低波動、容易小勝的股票，錯過少數大贏家。之後改成「扣成本的報酬」當標籤。",
    ),
    (
        "lgbm_net_h20_",
        "成本感知：20 日扣成本報酬（短窗口）",
        "預測未來 20 個交易日、扣掉交易成本後相對大盤的報酬（D-040、D-052、D-059），12 項特徵，2020 年起的資料。"
        "最好的月頻版本扣成本年化 +14.2%，但回撤 −40.8%，判定只當零件（D-066）。"
        "封存的兩版是發現資料缺陷之前訓練的；2026-03-05 那版訓練資料越過保留期邊界三天，作廢。"
        "2026-09-09 由下面的長窗口版取代（D-110）。",
    ),
    (
        "lgbm_rev_h15_",
        "營收驚奇（短窗口）",
        "預測下一次公布的月營收年增率，會不會超出「本月年增＋加速度」的預期（D-074）——預測的是基本面，不是股價。"
        "短窗口版因為幾支稀疏因子失效而作廢，由長窗口版取代。",
    ),
    (
        "lgbm_dd_h60_",
        "回撤風險（短窗口）",
        "預測未來 60 個交易日內最深會跌多少（排名）。短窗口版因為幾支稀疏因子失效而作廢，由長窗口版取代。",
    ),
    (
        "lgbm_lw_net_",
        "長窗口：20 日扣成本報酬（大盤精選用）",
        "同成本感知標籤，但資料拉長到 2015-09 起，訓練涵蓋 2018 Q4、2020、2022 三段下跌期（D-110）。"
        "同期測試 Rank IC +0.0629（短窗口 +0.0452）；模型沒看過的半年（2026-03～08）+0.0816，"
        "是整個研究唯一在沒看過的資料上站得住的結果（D-107）。目前大盤精選用它。"
        "「pre_t180」是補完 2020 Q1 因子資料之前的版本。",
    ),
    (
        "lgbm_lw_dd_",
        "長窗口：回撤風險",
        "預測未來 60 個交易日內最深跌幅的排名，原本當風控閘門（剔除最危險的 10%，D-111）。"
        "Rank IC 0.42 看起來很高，但光按 20 日波動排序就有 0.39——94% 是「波動大的股票跌得深」這個定義，"
        "模型多出來的只有約 +0.03（D-089）。現在每天照樣打分，兩條線都沒用。",
    ),
    (
        "lgbm_lw_rev_",
        "長窗口：營收驚奇",
        "長窗口版的營收驚奇模型，曾當營收類策略的輔助分數（D-112）；那類策略下架後不再每日打分（D-163）。",
    ),
    (
        "lgbm_lwc_",
        "長窗口＋籌碼特徵版",
        "回撤與營收驚奇兩個模型加入籌碼特徵的版本（D-104）。第二段檢驗沒過，結案（D-105）。",
    ),
    (
        "lgbm_lw_tb_",
        "買進模型：三重障礙",
        "預測 20 個交易日內先碰到 +10% 還是 −10%（e823c63f 是不對稱版：+10%／−5%），二元分類（D-120、D-121）。"
        "「_cal」是做過機率校準的版本。兩關診斷都沒過，不進回測。",
    ),
    (
        "lgbm_lw3_tb_",
        "買進模型：三重障礙（波動標準化）",
        "障礙改成「一倍自身波動」而不是固定 ±10%，23 項特徵。診斷沒過，收線。",
    ),
    (
        "lgbm_lw4_tb_",
        "買進模型：三重障礙（波動標準化＋籌碼）",
        "同上，特徵加到 41 項（含籌碼）；「_cal」是校準版。診斷沒過，收線。",
    ),
    (
        "lgbm_lw6_dp_",
        "下跌機率",
        "預測 20 個交易日內會不會跌 10%，41 項特徵（第六輪，D-131）。Rank IC 0.31，但贏不過單用 20 日波動，"
        "三條診斷全部未通過，收線（D-132）。",
    ),
    (
        "lgbm_tail",
        "右尾：60 日飆 50%",
        "預測 60 個交易日內會不會漲到 +50%（第九、十輪，D-143～D-150；A、B、B′、C 是特徵與資料期間不同的臂）。"
        "B 臂曾上架平台前向追蹤（D-147）。結論：前幾名的命中率看起來是 3 倍，但和同波動、同動能的股票比只有 1.08 倍——"
        "其實是動能篩選，不是額外的預測力。",
    ),
    (
        "lgbm_quietA",
        "安靜股：20 日飆漲機率（潛伏飆股用）",
        "只看「安靜又買得到」的股票（近 60 日漲不到 20%、波動不在最高的 30%、成交金額前 30%、股價 10 元以上），"
        "預測 20 個交易日內會不會漲到門檻（A／A22／Ap：+30%，A25：+25%，A20：+20%；第十二輪，D-151、D-152）。"
        "A20 的前 20 名碰到 +30% 的比例是同波動、同漲幅股票的 1.40 倍，選為潛伏飆股的模型（D-163）。"
        "A22 只用 2022 年起的資料（分點資料從那時才有），Ap 再加 3 支分點特徵；分點特徵只多 0.02～0.09 倍。2026-02-26 那版是事後檢驗用（D-162：訓練到 2026-02、考 3～9 月）。"
        "優勢在機率，不在平均報酬：九成五不會飆、單檔報酬中位數為負。",
    ),
    (
        "lgbm_hotB",
        "已飆股：會不會再跌 15%",
        "第十二輪的模型 B（D-151）：已經飆過的股票，20 個交易日內會不會再跌 15%。九年平均沒用，"
        "只在含 2026-07 下跌的保留段有用（學到的是大盤狀態）；B22、Bp 同樣是 2022 年起資料與加分點特徵的對照版。",
    ),
]

LABELS = {
    "rank_pct": "{h} 日報酬排名",
    "cost_adjusted_return": "{h} 日扣成本報酬",
    "revenue_surprise": "下次月營收的驚奇",
    "forward_drawdown": "{h} 日最深跌幅",
    "triple_barrier": "{h} 日內先漲還是先跌",
    "down_probability": "{h} 日內跌 {drop}",
    "tail_up": "{h} 日內漲到 +{up}",
}


def family(model_id: str) -> tuple[str, str]:
    for prefix, name, desc in FAMILIES:
        if model_id.startswith(prefix):
            return name, desc
    raise SystemExit(f"{model_id} 不屬於任何系列：在 FAMILIES 加一條說明")


def label_text(meta: dict) -> str:
    lab = meta.get("label") or {}
    kind = lab.get("transform") or "rank_pct"
    pct = lambda v: f"{round(v * 100)}%" if isinstance(v, (int, float)) else "?"  # noqa: E731
    text = LABELS.get(kind, kind).format(
        h=meta["horizon_days"], up=pct(lab.get("barrier_up")), drop=pct(lab.get("drop_threshold"))
    )
    if kind == "triple_barrier" and lab.get("vol_scaled"):
        text += "（±1 倍波動）"
    elif kind == "triple_barrier":
        text += f"（+{pct(lab.get('barrier_up'))}／−{pct(lab.get('barrier_down'))}）"
    return text


def oos_ic(meta: dict) -> tuple[str, int]:
    """樣本外 Rank IC：有整體值就用；分類模型沒有整體值時用各驗證窗的平均。回傳 (文字, 窗數)。"""
    wins = [
        x for x in meta.get("window_ics") or [] if isinstance(x, (int, float)) and not math.isnan(x)
    ]
    ic = meta.get("oos_rank_ic")
    if isinstance(ic, (int, float)) and not math.isnan(ic):
        return f"{ic:+.3f}", len(wins)
    if wins:
        return f"{sum(wins) / len(wins):+.3f}*", len(wins)
    return "—", 0


def status(rel: str, model_id: str) -> str:
    if rel.startswith("_archive/"):
        return "封存（資料缺陷前，留作證據）"
    if rel.startswith("_rejected/"):
        return "作廢：" + {
            "sparse-factors-dead": "稀疏因子失效",
            "vault-eroded-3-sessions": "越過保留期邊界",
        }.get(rel.split("__")[-1], rel.split("__")[-1])
    if rel.endswith("_pre_t180"):
        return "舊版（補資料前）"
    if rel.endswith("_cal"):
        return "研究（校準版）"
    return LIVE.get(model_id, "研究用，已收線")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", required=True, type=pathlib.Path, help="data/models 絕對路徑")
    ap.add_argument("--out", required=True, type=pathlib.Path, help="輸出的 markdown（絕對路徑）")
    args = ap.parse_args()
    rows: dict[str, list[str]] = {}
    count = 0
    for meta_path in sorted(args.models.glob("**/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rel = meta_path.parent.relative_to(args.models).as_posix()
        name, _ = family(meta["model_id"])
        ic, n = oos_ic(meta)
        kind = (
            meta.get("objective", "regression")
            .replace("regression", "迴歸")
            .replace("binary", "分類")
        )
        # 手機上表格會左右滑：重點（狀態、預測什麼、成績）放前面，很長的模型代號放最後一欄
        rows.setdefault(name, []).append(
            f"| {status(rel, meta['model_id'])} | {label_text(meta)} | {kind} | {len(meta['features'])} "
            f"| {meta['data_from']}～{meta['train_end']} | {ic} | {n} | `{rel}` |"
        )
        count += 1
    lines = [
        f"# 模型清冊：訓練過的所有模型（{dt.date.today().isoformat()}）",
        "",
        f"> 共 {count} 個模型（含封存與作廢），全部是 LightGBM。資料直接讀每個模型訓練時寫下的說明檔（`data/models/*/meta.json`），"
        "依用途分系列；每個系列先說它在預測什麼、結果如何，再列出每一個版本。上線中的只有三個（見「狀態」欄）。",
        "",
        "**怎麼讀「樣本外 IC」**：每一天只用更早的資料訓練、再對當天打分（walk-forward），分數和實際結果的排名相關。"
        "報酬類模型 +0.05 以上就算有用；標「*」的是分類模型（預測會不會發生），算法是預測機率和「有沒有發生」的排名相關，"
        "數字天生比較大，不能和報酬類直接比。回撤類模型的 IC 很高，主要來自「波動大的股票跌得深」這個定義。",
        "",
    ]
    for _, name, desc in FAMILIES:
        if name not in rows:
            continue
        lines += [
            f"## {name}",
            "",
            desc,
            "",
            "| 狀態 | 預測什麼 | 類型 | 特徵數 | 訓練資料 | 樣本外 IC | 驗證窗 | 模型代號 |",
            "|---|---|---|---:|---|---:|---:|---|",
            *rows.pop(name),
            "",
        ]
    args.out.write_text("\n".join(lines), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(f"[OK] {args.out}（{count} 個模型）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
