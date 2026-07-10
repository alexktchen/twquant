"""供應層（SPEC-009）：Parquet 湖 → PostgreSQL 衍生讀取層。

data/ 是允許 IO 的白名單層。此子套件負責 PG schema、同步（parquet→PG upsert）與
因子物化；PG 僅供儀表板展示、可隨時 rebuild，禁回饋因子/回測/策略（IO 邊界靜態測試把關）。
"""

from __future__ import annotations
