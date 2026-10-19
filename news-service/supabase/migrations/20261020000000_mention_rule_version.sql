-- T-402（2026-10-20）：比對規則改版（停用詞、擋字）要能重掃。原本主鍵只有 (item_id, content_sha)，
-- 同一則同一版內容只能比一次；改成主鍵含 rule_version——新規則再比一輪，舊規則的結果留著（只增不改），讀取只看目前規則。
alter table news.mentions drop constraint mentions_pkey;
alter table news.mentions add primary key (item_id, entity_id, content_sha, rule_version);
alter table news.mention_runs drop constraint mention_runs_pkey;
alter table news.mention_runs add primary key (item_id, content_sha, rule_version);
