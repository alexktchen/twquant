-- 從新聞學關係（2026-10-20，src/cooccur.ts）：graph_edges 多一種線 cooccur（新聞常一起提到），由 ingest 第四步維護（source＝news_cooccur）。
alter table news.graph_edges drop constraint graph_edges_type_check;
alter table news.graph_edges add constraint graph_edges_type_check
  check (type in ('supplier','belongs','member','peer','theme','cooccur'));
