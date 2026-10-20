"""新聞因子（T-414／SPEC-018 §5／D-201）：薄因子，只讀 news_features 表——每日逐股特徵由資料層
`data/news_features.py` 先算好（含 available_at，t 日那列＝t 日 cutoff_hour 之前的新聞），這裡不做任何新計算。
純函數、無 IO；跟 ml_scores 同一套路（黃金規則 1、IO 靜態閘門）。

方向都設 1：只給 LightGBM 當特徵、不當線性訊號（估值因子的教訓：IC 顯著不代表分位報酬單調）。
缺值：沒新聞＝null、不補 0；只有 count_1d 的 0 是真值（沒人講它）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

# 主題數；跟 configs/news_factors.yaml topics.k 一樣（tests/test_news_factors.py 核對），因子名在匯入時就要定下來
TOPIC_K = 16

_SIMPLE: dict[str, str] = {
    "count_1d": "今天多少人在講它：log1p(t 日提到它的新聞則數)；沒新聞＝0 是真值。",
    "attention_5d": "比它平常熱多少：近 5 日則數 ÷ 近 60 日日均（都 +1），取 log。",
    "attention_20d": "比它平常熱多少：近 20 日則數 ÷ 近 60 日日均（都 +1），取 log。",
    "novelty": "是新消息還是舊聞重炒：1 − 近 5 日新聞對它過去 60 日新聞的最大 cosine；沒舊聞＝null。",
    "sent_1d": "講它的是好話還是壞話：近 1 日情緒平均（p_pos − p_neg）；沒新聞＝null。",
    "sent_5d": "講它的是好話還是壞話：近 5 日情緒平均；沒新聞＝null。",
    "sent_shift": "風向有沒有變：近 5 日情緒 − 再往前 20 日的情緒。",
    "sector_heat": "整個族群在不在話題上：族群近 5 日被提到的則數，對族群近 120 日的 z 分數。",
    "mops_count_5d": "公告多不多：近 5 日它的重大訊息則數。",
    "mops_novelty": "公告是例行還是少見：1 − 最新一則公告對它過去 180 日公告的最大 cosine。",
}


def _read(view: PitView, col: str) -> pl.DataFrame:
    return (
        view.table("news_features")
        .select("date", "stock_id", pl.col(col).alias("value"))
        .drop_nulls("value")
        .collect()
        .sort(["date", "stock_id"])
    )


def _register(col: str, doc: str) -> None:
    @factor(
        name=f"news.{col}", category="news", direction=1, tables=["news_features"], lookback_days=0
    )
    def fn(view: PitView, params: dict[str, object]) -> pl.DataFrame:
        return _read(view, col)

    fn.__name__ = f"news_{col}"
    fn.__doc__ = doc


for _col, _doc in _SIMPLE.items():
    _register(_col, _doc)
for _k in range(1, TOPIC_K + 1):
    _register(
        f"topic_{_k:02d}",
        f"它最近的新聞偏哪一類：近 5 日新聞向量平均對第 {_k} 個主題中心的 cosine。",
    )

NEWS_FACTORS: tuple[str, ...] = tuple(f"news.{c}" for c in _SIMPLE) + tuple(
    f"news.topic_{k:02d}" for k in range(1, TOPIC_K + 1)
)
