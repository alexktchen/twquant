-- T-402／T-404：實體、關係線（參考資料，可更新 valid_to）；提及與比對紀錄（只增）。SPEC-017 §5～§7、D-200。
-- entities／graph_edges 由 scripts/news_sync_entities.py 從主庫與 configs/news_entities.yaml、news_edges.yaml 同步：
-- 實體可改名、線不再成立時填 valid_to（不刪），所以不擋 UPDATE；mentions／mention_runs 是比對結果，只增不改。

-- 實體：台股、美股、國外公司、族群、主題。id 帶前綴。
create table news.entities (
  id          text primary key,            -- tw:2330 / us:NVDA / co:samsung / grp:半導體 / th:ev
  type        text not null check (type in ('tw','us','company','group','theme')),
  label       text not null,               -- 顯示名（中文）
  sub         text,                        -- 代號、ticker、國家、「族群」「主題」
  aliases     text[] not null default '{}',
  source      text not null,               -- stock_meta / stock_group / config
  valid_from  timestamptz not null default now(),
  valid_to    timestamptz
);

-- 關係線：supplier＝國外公司→台灣供應商；belongs＝台股→族群；peer＝同業；theme＝主題→族群／台股。
-- member 不存（由 belongs 反推，src/graph.ts expandEdges）。draft＝AI 草擬、使用者還沒核可（網站畫虛線）。
create table news.graph_edges (
  src text not null, dst text not null,
  type   text not null check (type in ('supplier','belongs','member','peer','theme')),
  status text not null check (status in ('approved','draft')),
  label  text,                             -- 線上的說明（同族群線寫族群名）
  rank   int,                              -- belongs：這檔在族群裡的成交金額名次（1 最大）
  source text not null,
  valid_from timestamptz not null default now(),
  valid_to   timestamptz,
  primary key (src, dst, type, valid_from)
);
create index graph_edges_active on news.graph_edges (src) where valid_to is null;

-- 一則新聞「某一版內容」直接提到哪些實體（一度）。內容改版 → 新的 content_sha 重比一次，舊的留著。
create table news.mentions (
  item_id      text not null references news.raw_items (id),
  entity_id    text not null,
  content_sha  text not null,              -- 比對的是哪一版內容
  method       text not null,              -- alias
  evidence     text not null,
  rule_version text not null,
  created_at   timestamptz not null default now(),
  primary key (item_id, entity_id, content_sha)
);

-- 這一版內容比對過了（含 0 命中），免得每次重掃。
create table news.mention_runs (
  item_id      text not null references news.raw_items (id),
  content_sha  text not null,
  n            int not null,
  rule_version text not null,
  ran_at       timestamptz not null default now(),
  primary key (item_id, content_sha)
);

create trigger mentions_append_only before update or delete on news.mentions
  for each row execute function news.forbid_change();
create trigger mention_runs_append_only before update or delete on news.mention_runs
  for each row execute function news.forbid_change();

-- 同樣開 RLS、不給政策（只有 Edge Function 與同步腳本用資料庫連線讀寫）。
alter table news.entities enable row level security;
alter table news.graph_edges enable row level security;
alter table news.mentions enable row level security;
alter table news.mention_runs enable row level security;
