"""模型的樣本外 IC 勝率——`configs/model_catalog.yaml` 那幾個數字的重算工具（D-122）。

「IC 勝率」＝當天橫斷面 RankIC > 0 的訊號日比例，用的是 repo 自己的
`twquant.ml.evaluate.daily_rank_ic`，不另外發明公式；`rank_ic` 是逐日 IC 的平均，
與 `meta.json` 的 `oos_rank_ic` 同一個定義（四個模型實測逐位元相同）。

只讀 `data/models/<id>/{meta.json,oos_scores.parquet}`，不寫任何檔案：輸出是一段可貼回
目錄檔的 YAML。同時比對目錄裡登記的 `artifact_fingerprint` 與模型現在的指紋——
不符就是模型重訓過而數字沒跟著更新（T-180 的情形），以非零碼結束。

    uv run python scripts/model_ic_hitrate.py --models D:/project/twquant/data/models
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twquant.config import ModelCatalogConfig, load_config  # noqa: E402
from twquant.ml.evaluate import daily_rank_ic  # noqa: E402

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "model_catalog.yaml"


def _measure(model_dir: Path) -> tuple[dict[str, object], str]:
    """回傳（可貼進目錄檔的量測區塊, 模型現在的指紋）。"""
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    scored = pl.read_parquet(model_dir / "oos_scores.parquet")
    ic = daily_rank_ic(scored, "score", "label").drop_nulls("ic")
    if ic.height == 0:
        raise ValueError(f"{model_dir.name}：oos_scores.parquet 沒有可用的 IC 日")
    return (
        {
            "label": "樣本外",
            "rank_ic": round(float(ic["ic"].mean() or 0.0), 6),
            "hit_rate": round(float((ic["ic"] > 0).mean() or 0.0), 6),
            "n_days": ic.height,
            "period_start": str(ic["date"].min()),
            "period_end": str(ic["date"].max()),
            "source": "scripts/model_ic_hitrate.py",
        },
        str(meta["artifact_fingerprint"]),
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="模型庫根目錄（給絕對路徑，見 CLAUDE.md §0.2）")
    parser.add_argument("--catalog", default=str(_CONFIG), help="要比對的模型目錄檔")
    args = parser.parse_args()

    catalog = load_config(args.catalog, ModelCatalogConfig)
    root = Path(args.models)
    drifted: list[str] = []
    for model_id, entry in catalog.models.items():
        model_dir = root / model_id
        if not (model_dir / "oos_scores.parquet").exists():
            print(f"# {model_id}：模型庫裡沒有這個模型的 oos_scores.parquet，跳過")
            continue
        block, fingerprint = _measure(model_dir)
        print(f"\n# {model_id}　（{entry.name}）")
        if fingerprint != entry.artifact_fingerprint:
            drifted.append(model_id)
            print(
                f"# ⚠ 指紋不符：目錄登記 {entry.artifact_fingerprint}、模型現在是 {fingerprint}"
                "——模型重訓過，下面的數字要連同 artifact_fingerprint 一起更新"
            )
        for key, value in block.items():
            print(f"    {key}: {value}")
        print(f"    measured_on: <今天>")

    if drifted:
        print(f"\n指紋不符：{'、'.join(drifted)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
