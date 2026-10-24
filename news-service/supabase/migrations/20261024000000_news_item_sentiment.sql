-- 新聞情緒（D-205 走 A／D-218，2026-10-24）：雲端 qwen3 判斷「這則新聞對這檔台股」好（1）／中性（0）／壞（−1）。
-- 一版內容（content_sha）＋一版提示詞（prompt_version）一列；換提示詞版本就重問，舊列留著。可重算的衍生表：不擋 UPDATE。
create table news.item_sentiment (
  item_id        text not null references news.raw_items (id) on delete cascade,
  entity_id      text not null,
  content_sha    text not null,
  label          smallint not null check (label in (-1, 0, 1)),
  model          text not null,
  prompt_version text not null,
  created_at     timestamptz not null default now(),
  primary key (item_id, entity_id, content_sha, prompt_version)
);
create index item_sentiment_entity on news.item_sentiment (entity_id, prompt_version);
alter table news.item_sentiment enable row level security;
