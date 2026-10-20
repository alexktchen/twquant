-- T-418：新聞全景（星雲圖）的快照。scripts/news_galaxy.py 在排程裡算好（3D 座標、主題群與名稱、亮點）整包寫進來，
-- 網站讀最新一筆（/v1/galaxy）。可重算的產物，只留最近幾筆（腳本自己刪舊的），不擋 UPDATE／DELETE。
create table news.galaxy_snapshots (
  built_at timestamptz primary key,
  items    int not null,
  data     jsonb not null
);
alter table news.galaxy_snapshots enable row level security;
