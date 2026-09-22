"""匯入即註冊所有內建因子。

任何需要「全部因子都可用」的入口（CLI ic-report、儀表板因子健檢）只要
`import twquant.factors.builtins` 一次，即完成 price/chip/fundamental 的註冊。
單一模組多次匯入由 Python 匯入快取去重，不會重複註冊。
"""

from __future__ import annotations

from twquant.factors import (  # 匯入觸發 @factor 註冊
    broker,
    chip,
    fundamental,
    industry,
    ml,
    oscillators,
    patterns,
    price,
    quality,
    volatility,
    volume,
)

__all__ = [
    "broker",
    "chip",
    "fundamental",
    "industry",
    "ml",
    "oscillators",
    "patterns",
    "price",
    "quality",
    "volatility",
    "volume",
]
