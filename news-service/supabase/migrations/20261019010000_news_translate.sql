-- T-401／D-200：翻譯與 Workers AI 用量帳（SPEC-017 §4）。同樣只增不改。

-- 一則新聞「某一版內容」的翻譯結果；同一版只翻一次（失敗的也不重翻，內容改版才再翻）。
create table news.translations (
  item_id        text not null references news.raw_items (id),
  content_sha    text not null,             -- 翻的是哪一版（raw_items 或 raw_item_versions 的 content_sha）
  title_zh       text,                      -- 沒過檢查 → null（網站顯示原文標「未譯」）
  summary_zh     text,
  status         text not null check (status in ('ok', 'failed')),
  checks         jsonb not null,            -- 擋與只記錄的檢查結果、自動修正了什麼
  model_id       text not null,
  prompt_version text not null,
  translated_at  timestamptz not null,
  primary key (item_id, content_sha)
);
create index translations_translated_at on news.translations (translated_at desc);

-- 新聞這邊的 Workers AI 用量（D-200 §五：每天 2,500 Neurons，UTC 日界）。
create table news.llm_usage (
  ts            timestamptz not null,
  task          text not null,
  model_id      text not null,
  input_tokens  integer not null,
  output_tokens integer not null,
  neurons       double precision not null
);
create index llm_usage_ts on news.llm_usage (ts);

create trigger translations_append_only before update or delete on news.translations
  for each row execute function news.forbid_change();
create trigger llm_usage_append_only before update or delete on news.llm_usage
  for each row execute function news.forbid_change();

alter table news.translations enable row level security;
alter table news.llm_usage enable row level security;
