-- T-421：新聞類型。每則新聞一列，由 ingest 第六步用本機訓好的探針（config/news_types.json）對向量算出；
-- version＝探針版本，換版本就重算（舊列留著當紀錄，查詢只看現行版本）。可重算的衍生表：不擋 UPDATE。
create table news.item_types (
  item_id     text not null references news.raw_items (id) on delete cascade,
  version     text not null,
  type        text not null,
  confidence  double precision not null,
  created_at  timestamptz not null default now(),
  primary key (item_id, version)
);
create index item_types_type on news.item_types (version, type);
alter table news.item_types enable row level security;
