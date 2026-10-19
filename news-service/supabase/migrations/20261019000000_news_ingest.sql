-- T-400／D-200：新聞服務的進料表（SPEC-017 §2）。只增不改——由觸發器擋 UPDATE／DELETE，不靠程式自律。
-- 資料庫：新聞服務自己的 Supabase 專案（同帳號第二個免費專案）。schema news 不開放給 PostgREST（只有 Edge Function 用資料庫連線讀寫）。

create schema if not exists news;

-- 一則新聞第一次被抓到的樣子。first_seen_at＝我們第一次抓到的時間（研究用的可得時間），之後不再改。
create table news.raw_items (
  id            text primary key,          -- sha256(source + '\n' + guid) 前 16 碼
  source        text not null,             -- configs/news.yaml 的來源代號
  guid          text not null,
  url           text not null,
  title_orig    text not null,
  summary_orig  text,
  lang          text not null check (lang in ('en', 'zh')),
  published_at  timestamptz,               -- 來源給的，只供顯示；Nikkei Asia 沒有
  first_seen_at timestamptz not null,
  content_sha   text not null,
  unique (source, guid)
);
create index raw_items_first_seen on news.raw_items (first_seen_at desc);

-- 來源後來改了標題或摘要：新增一版，不覆寫原本那列。
create table news.raw_item_versions (
  item_id      text not null references news.raw_items (id),
  title_orig   text not null,
  summary_orig text,
  content_sha  text not null,
  seen_at      timestamptz not null,
  primary key (item_id, seen_at)
);

-- 每個來源每次執行一列（成功、抓到幾則、新增幾則、錯誤）。/v1/health 讀這張。
create table news.ingest_runs (
  run_id       text not null,
  source       text not null,
  started_at   timestamptz not null,
  ok           boolean not null,
  http_status  integer,
  fetched      integer not null,
  new_items    integer not null,
  new_versions integer not null,
  error        text,
  primary key (run_id, source)
);
create index ingest_runs_source_started on news.ingest_runs (source, started_at desc);

-- 條件式請求用的 ETag／Last-Modified（唯一一張會更新的表：它不是資料，是抓取的狀態）。
create table news.source_state (
  source        text primary key,
  etag          text,
  last_modified text,
  updated_at    timestamptz not null default now()
);

create function news.forbid_change() returns trigger language plpgsql as $$
begin
  raise exception '新聞資料只增不改（D-200）：% 不允許 %', tg_table_name, tg_op;
end
$$;
create trigger raw_items_append_only before update or delete on news.raw_items
  for each row execute function news.forbid_change();
create trigger raw_item_versions_append_only before update or delete on news.raw_item_versions
  for each row execute function news.forbid_change();
create trigger ingest_runs_append_only before update or delete on news.ingest_runs
  for each row execute function news.forbid_change();

-- 開 RLS、不給任何政策：anon／authenticated 一律讀不到（防萬一 schema 被誤開給 PostgREST）。
alter table news.raw_items enable row level security;
alter table news.raw_item_versions enable row level security;
alter table news.ingest_runs enable row level security;
alter table news.source_state enable row level security;
