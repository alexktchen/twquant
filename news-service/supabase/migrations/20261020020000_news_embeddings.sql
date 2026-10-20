-- T-410／D-201／SPEC-018 §3.1：新聞向量（bge-m3，1024 維 halfvec）與同事件群。
-- 只改不得：向量算了不重算、事件群只新併舊不回頭重分 → 擋 UPDATE；DELETE 留給「超過一年的向量刪掉」（SPEC-018 §3.1，之後的排程）。
create extension if not exists vector with schema extensions;

create table news.embeddings (
  item_id    text primary key references news.raw_items (id),
  model      text not null,                    -- Workers AI 模型字串（@cf/baai/bge-m3）
  text_sha   text not null,                    -- 算向量用的那段文字（有譯文用譯文）的 sha256
  vec        extensions.halfvec(1024) not null,
  created_at timestamptz not null default now()
);
-- 相似新聞、語意搜尋：cosine 距離的 HNSW
create index embeddings_vec_hnsw on news.embeddings
  using hnsw (vec extensions.halfvec_cosine_ops) with (m = 16, ef_construction = 64);

-- 同事件群：cluster_id＝群裡第一則的 item_id；seed_item_id＝併進來時最像的那則（自己開群 → null）
create table news.clusters (
  item_id      text primary key references news.raw_items (id),
  cluster_id   text not null,
  seed_item_id text references news.raw_items (id),
  score        double precision,
  created_at   timestamptz not null default now()
);
create index clusters_cluster_id on news.clusters (cluster_id);

create trigger embeddings_no_update before update on news.embeddings
  for each row execute function news.forbid_change();
create trigger clusters_no_update before update on news.clusters
  for each row execute function news.forbid_change();

alter table news.embeddings enable row level security;
alter table news.clusters enable row level security;
