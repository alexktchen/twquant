"""報表與落地層（IO）。

純運算層（factors/strategy/backtest/portfolio）回傳資料結構，由本層負責落地：
research_log、IC 報告、回測報告等。與純運算層分離，符合 IO 邊界（CLAUDE.md §4）。
"""
