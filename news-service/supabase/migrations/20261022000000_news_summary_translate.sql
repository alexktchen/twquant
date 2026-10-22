-- D-207（2026-10-22）：排程只翻標題，摘要等有人點開那則才翻（/v1/summary）。同樣只增不改。
-- news.translations 是只增表、一版一列，不能回頭補摘要，所以點開翻的摘要另放一張。

-- 一則新聞「某一版內容」的摘要譯文；同一版只翻一次（沒過檢查的也記下來不再翻，內容改版才再翻）。
-- 額度用完、模型呼叫失敗不記（下次點開再翻）。
create table news.summary_translations (
  item_id        text not null references news.raw_items (id),
  content_sha    text not null,             -- 翻的是哪一版（raw_items 或 raw_item_versions 的 content_sha）
  summary_zh     text,                      -- 沒過檢查 → null（網站顯示英文摘要）
  status         text not null check (status in ('ok', 'failed')),
  checks         jsonb not null,
  model_id       text not null,
  prompt_version text not null,
  translated_at  timestamptz not null,
  primary key (item_id, content_sha)
);

create trigger summary_translations_append_only before update or delete on news.summary_translations
  for each row execute function news.forbid_change();

alter table news.summary_translations enable row level security;
