"""供應層 PG 連線與健康探測（SPEC-009 §1/§5）。

DSN 本體只在環境變數（cfg.dsn_env → .env，黃金規則 9）；此模組解析、連線、探測。
唯 data/serving 與 monitoring/pg_store 得 import 本模組（IO 邊界靜態測試把關）。
"""

from __future__ import annotations

import os
from typing import Any

import psycopg

from twquant.config import ServingConfig
from twquant.errors import ConfigError


def resolve_dsn(cfg: ServingConfig) -> str:
    """從 cfg.dsn_env 指名的環境變數取 DSN；未設 → ConfigError（含變數名）。"""
    dsn = os.environ.get(cfg.dsn_env)
    if not dsn:
        raise ConfigError(f"供應層 DSN 環境變數未設：{cfg.dsn_env}（見 .env / SPEC-009 §1）")
    return dsn


def connect(cfg: ServingConfig) -> Any:
    """開一條 PG 連線（呼叫端負責關閉或用 with）。

    `prepare_threshold=None`：Supabase transaction pooler（:6543）不支援 prepared
    statements（T-057）；DSN 指向本機 PG 時停用 prepared 亦無害（COPY/upsert 為主）。
    """
    return psycopg.connect(resolve_dsn(cfg), prepare_threshold=None)


def resolve_cloud_dsn(cfg: ServingConfig) -> str:
    """雲端（Supabase）DSN：cfg.cloud.dsn_env 指名的環境變數；未設 → ConfigError。"""
    dsn = os.environ.get(cfg.cloud.dsn_env)
    if not dsn:
        raise ConfigError(f"雲端 DSN 環境變數未設：{cfg.cloud.dsn_env}（見 .env / D-030）")
    return dsn


def connect_cloud(cfg: ServingConfig) -> Any:
    """開一條雲端 PG 連線。Supabase transaction pooler（:6543）不支援 prepared
    statements → `prepare_threshold=None` 停用（T-055/T-057）。"""
    return psycopg.connect(resolve_cloud_dsn(cfg), prepare_threshold=None)


def ping(cfg: ServingConfig) -> bool:
    """健康探測：能連上且 SELECT 1 成功回 True，否則 False（供 API 降級判斷）。"""
    try:
        with psycopg.connect(resolve_dsn(cfg)) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return bool(cur.fetchone() == (1,))
    except (psycopg.Error, ConfigError):
        return False
