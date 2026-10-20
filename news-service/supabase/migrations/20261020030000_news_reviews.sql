-- T-403／SPEC-017 §6、D-200 §三：核可紀錄（只增）。AI 草擬的線要擁有者核可才算數。
-- 核可 → 草稿那條 graph_edges 補 valid_to、另插一條 status approved（valid_from＝核可時間）；退回 → 草稿補 valid_to。
-- 每個決定記一筆在這裡，同一條線以最新一筆為準；scripts/news_sync_entities.py 同步時照它，不會把核可過的改回草稿、退回的又長出來。
create table news.reviews (
  id          bigserial primary key,
  target      text not null check (target in ('edge', 'mention')),
  key         text not null,                 -- edge：src|dst|type；mention（T-411）：item_id|entity_id
  decision    text not null check (decision in ('approved', 'rejected')),
  reviewer    text not null,                 -- 核可者的登入 email（網站驗過 Cloudflare Access 權杖）
  reviewed_at timestamptz not null
);
create index reviews_target_key on news.reviews (target, key, reviewed_at desc);

create trigger reviews_append_only before update or delete on news.reviews
  for each row execute function news.forbid_change();
alter table news.reviews enable row level security;
