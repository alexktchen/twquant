-- 萬安政策島 TD：Supabase 資料表與權限（在 Supabase 後台 SQL Editor 整段貼上執行一次）
-- 前置：Authentication → Sign In / Providers → 打開「Allow anonymous sign-ins」

-- 每位訪客一列戰績；id = 匿名登入的 auth.uid()，只能改自己那列
create table if not exists public.heroes (
  id          uuid primary key references auth.users(id) on delete cascade,
  nick        text not null default '' check (char_length(nick) <= 24),
  cls         text not null default 'chief' check (cls in ('chief','driver','fireman','volunteer','engineer')),
  rounds      integer not null default 0 check (rounds >= 0),
  kills       integer not null default 0 check (kills >= 0),
  finals      integer not null default 0 check (finals >= 0),
  last_issue  text,
  last_at     timestamptz not null default now()
);
alter table public.heroes enable row level security;
drop policy if exists "heroes are public" on public.heroes;
drop policy if exists "insert own hero" on public.heroes;
drop policy if exists "update own hero" on public.heroes;
create policy "heroes are public" on public.heroes for select using (true);
create policy "insert own hero"   on public.heroes for insert with check (auth.uid() = id);
create policy "update own hero"   on public.heroes for update using (auth.uid() = id) with check (auth.uid() = id);

-- 政策與議題：所有人可讀；寫入只開給後台（Supabase Table Editor，或之後加管理員登入）
create table if not exists public.policies (id text primary key, data jsonb not null, updated_at timestamptz not null default now());
create table if not exists public.issues   (id text primary key, data jsonb not null, updated_at timestamptz not null default now());
alter table public.policies enable row level security;
alter table public.issues   enable row level security;
drop policy if exists "policies are public" on public.policies;
drop policy if exists "issues are public"   on public.issues;
create policy "policies are public" on public.policies for select using (true);
create policy "issues are public"   on public.issues   for select using (true);

-- 讓英雄榜即時更新
do $$ begin
  alter publication supabase_realtime add table public.heroes;
exception when duplicate_object then null; end $$;

-- 範例：從後台新增一筆政策（data 欄位格式同 js/config.js 的 policies）
-- insert into public.policies (id, data) values ('metro-east', '{"title":"捷運東環段","tag":"#交通","model":"station","district":"neihu","summary":"東環段持續推進。","facts":["規劃中"]}');
