-- T-400：每 30 分鐘（含半夜）呼叫 ingest Edge Function。不放 migrations：要用到專案網址與金鑰（存在 Vault，不進版控）。
-- 在新聞專案的 SQL Editor 執行一次（先把下面兩個 <…> 換掉；金鑰同 Edge Function 祕密 NEWS_INGEST_KEY）：
--
--   select vault.create_secret('https://<專案代號>.supabase.co', 'news_project_url');
--   select vault.create_secret('<NEWS_INGEST_KEY>', 'news_ingest_key');
--
-- 改時間：先 select cron.unschedule('news-ingest'); 再重跑本檔。查執行紀錄：select * from cron.job_run_details order by start_time desc limit 20;

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.schedule(
  'news-ingest',
  '*/30 * * * *',  -- UTC 每 30 分鐘；台北同樣是每小時的 00 與 30 分
  $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'news_project_url') || '/functions/v1/ingest',
    headers := jsonb_build_object(
      'content-type', 'application/json',
      'x-news-key', (select decrypted_secret from vault.decrypted_secrets where name = 'news_ingest_key')
    ),
    body := '{}'::jsonb,
    -- 函式先回 202、工作在背景做，所以 5 秒內一定回得來
    timeout_milliseconds := 5000
  );
  $$
);
