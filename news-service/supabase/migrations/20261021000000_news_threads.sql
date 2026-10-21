-- T-420：事件追蹤。cluster_centroids＝每個事件群的中心向量（成員加入時更新）；threads＝事件群串成的線（只新串舊）。
-- 兩張都是可重算的衍生表：不擋 UPDATE。既有事件群先各自一條線（thread_id＝cluster_id），之後新開的群才會往回串。
create table news.cluster_centroids (
  cluster_id    text primary key,
  n             int not null,
  vec           extensions.halfvec(1024) not null,
  first_seen_at timestamptz not null,
  last_seen_at  timestamptz not null
);
create index cluster_centroids_vec_hnsw on news.cluster_centroids
  using hnsw (vec extensions.halfvec_cosine_ops) with (m = 16, ef_construction = 64);
create index cluster_centroids_last_seen on news.cluster_centroids (last_seen_at);

create table news.threads (
  cluster_id      text primary key,
  thread_id       text not null,
  seed_cluster_id text,
  score           double precision,
  created_at      timestamptz not null default now()
);
create index threads_thread_id on news.threads (thread_id);

insert into news.cluster_centroids (cluster_id, n, vec, first_seen_at, last_seen_at)
select c.cluster_id, count(*), avg(e.vec)::extensions.halfvec(1024), min(i.first_seen_at), max(i.first_seen_at)
from news.clusters c join news.embeddings e on e.item_id = c.item_id join news.raw_items i on i.id = c.item_id
group by c.cluster_id;
insert into news.threads (cluster_id, thread_id)
select distinct cluster_id, cluster_id from news.clusters;

alter table news.cluster_centroids enable row level security;
alter table news.threads enable row level security;
