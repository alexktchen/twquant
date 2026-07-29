# OPS — raw 歸檔與還原(保命線)

出處:D-030(全雲端搬遷)/ D-033(Drive 容量策略)。任務:T-053(全史)、T-054(每日增量)。
**這條鏈是唯一保命線**:券商分點(broker_trades)資料過期後無法從來源重抓,歸檔絕不能斷。

## 1. 檔案佈局(Drive)

```
twquant-archive/
  base/twquant-full-YYYYMMDD.dump   # 全史基底(pg_dump -Fc,約 1.4 GB/15 GB 庫)
  daily/YYYY/YYYYMMDD/              # 每日增量(T-054,CLI archive-daily)
    <table>.csv.gz                  #   各原始表單日切片(ORDER BY 主鍵、gzip mtime=0,
    manifest.json                   #   同庫狀態重跑 bit-identical);manifest 記各表列數
```

增量範圍 = 原始鏡射表 + fubon_fetch_log/screener_daily + stock_meta 全量快照;
因子表(factor_values/composite_daily/factor_cross_stats)可重算、不歸檔。
實測單日 ~525 KB(broker_trades 53k 列 → 354 KB)。**增量必須在 serving sync 之後跑**
(pipeline 尾波/GHA 步驟順序),否則當日列尚未進 PG、歸檔到空切片。
增量設計成「可合併進基底」:日後容量吃緊時,把最舊一段增量 `archive-restore`
進基底庫再重做 base dump,即可刪除該段日檔(D-033 選項 B)。

## 2. 全史 dump(來源:本機 docker PG)

```bash
docker exec twquant-pg-1 pg_dump -U twquant -d twquant -Fc -Z 6 -f /tmp/twquant-full-YYYYMMDD.dump
docker cp twquant-pg-1:/tmp/twquant-full-YYYYMMDD.dump D:/project/twquant/data/archive/
docker exec twquant-pg-1 rm /tmp/twquant-full-YYYYMMDD.dump
```

實測(2026-07-29):15 GB 庫 → 1.3 GB dump,約 4 分鐘。
pg_dump 為 MVCC 一致性快照:dump 期間的併發寫入不會出現在 dump 內,
比對列數時以 dump「開始時刻」的庫狀態為準。

## 3. 還原(演練與災難復原同一套步驟)

```bash
docker run -d --name twquant-pg-restore -e POSTGRES_USER=twquant -e POSTGRES_PASSWORD=twquant -e POSTGRES_DB=twquant postgres:16-alpine
docker cp <dump 檔> twquant-pg-restore:/tmp/full.dump
docker exec twquant-pg-restore pg_restore -U twquant -d twquant --no-owner -j 4 /tmp/full.dump
```

驗收比對(還原庫 vs 來源庫在 dump 時刻的值):

```sql
SELECT count(*), min(date), max(date) FROM serving.broker_trades;  -- 最大表
SELECT count(*) FROM serving.prices_daily;
SELECT count(*) FROM serving.factor_values;
```

2026-07-29 基準(dump 時刻):broker_trades 55,340,144 列(2022-01-03~2026-07-23)、
prices_daily 9,069,140、factor_values 22,914,835、composite_daily 1,669,390、
institutional_daily 247,441、margin_daily 212,230、monthly_revenue 137,934、
fubon_fetch_log 2,147,839、screener_daily 17,450。

## 4. 上傳 Google Drive(rclone)

一次性設定(需要使用者本人跑 OAuth,AI 代理不得代操作):

```bash
rclone config create gdrive drive scope=drive.file
```

(會開瀏覽器要求 Google 授權;`drive.file` scope 只能存取 rclone 自己建立的檔案,
最小權限。)設定完成後 token 存於 `%APPDATA%\rclone\rclone.conf`——此檔內容日後
也是 GHA Secret(`RCLONE_CONF`),供 T-054/T-056 在 GHA 內上傳增量。

上傳:

```bash
rclone copy D:/project/twquant/data/archive/twquant-full-20260729.dump gdrive:twquant-archive/base/ --progress
rclone check D:/project/twquant/data/archive/daily gdrive:twquant-archive/daily --one-way
```

⚠ rclone 的**共用 Google client_id 將於 2026 年內退役**(每次執行都會印 NOTICE):
退役前需照 https://rclone.org/drive/#making-your-own-client-id 建自己的 client_id
並 `rclone config update gdrive client_id=... client_secret=...` 換上(token 需重授權)。
GHA 用的 rclone.conf Secret 屆時同步更新。

## 5. 每日增量(T-054 CLI)

```bash
python -m twquant archive-daily --date 2026-07-29          # 預設落 <data_dir>/archive/daily
python -m twquant archive-restore --date 2026-07-29        # 還原(ON CONFLICT 首錄保留,冪等)
rclone copy <data_dir>/archive/daily gdrive:twquant-archive/daily/ --progress
```

## 6. 全史回測/研究需要完整歷史時

本機(或任何有 docker 的機器)照 §3 還原 base dump,再從 Drive 抓 daily/ 後按日期
順序逐日 `archive-restore` 補到目標日期,`TWQUANT_PG_DSN` 指向還原庫即可。
