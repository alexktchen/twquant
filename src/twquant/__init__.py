"""twquant — 台股系統性量化交易平台。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("twquant")
except PackageNotFoundError:  # 尚未安裝（開發樹直接執行）
    __version__ = "0.0.0+dev"
