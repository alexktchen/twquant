# 交接：2026-10-07 晚——資料補齊做到一半（九月已修大半、十月五天沒跑）、T-327 未過全套閘門

分支：`claude/data-catchup-1007`（worktree `d-159-06d55d`，從 origin/main `31a2845` 開）。
使用者在補資料途中喊停、要求交接。本文件是停下那一刻的實際狀態（2026-10-07 23:15 量）。

## 0. 一句話

電腦／Docker 在 10-01 ~ 10-07 停擺，資料停在 09-30；而 09-24 起排程本來就有三個會漏資料的洞。
**九月的洞補了大半，十月五個交易日完全沒跑。下一次排程是 10-08 19:00，它只跑 10-08 當天**——
不先補十月，10-08 的因子回看會跨過五天空白。接手第一件事見 §4。

## 1. 資料現況

| 項目 | 狀態 |
|---|---|
| 行情等每日表（價格、法人、估值、券商…） | 到 **09-30**。10-01、10-02、10-05、10-06、10-07 五個交易日全缺。09-25（中秋）、09-28（教師節）休市，不是洞 |
| 融資融券 `margin_daily` 09-24／29／30 | ✅ 已補（每天 1,297 列，已鏡射進 PG） |
| 籌碼因子 `chip.margin_velocity_5d`、`chip.short_margin_ratio` | ✅ 09-29／30 已重算。09-24 重算後與原值**逐位相同**——因子用 T−1 融資，09-24 的值本來就對 |
| 盤整五因子（`price.bb_squeeze_120` 等，A20 要用） | 09-29 ✅；**09-30 ❌**（物化跑到一半被中止） |
| `ml_scores` | 生產排序模型 09-29／30 有；**風控 `lw_dd`、輔助 `lw_rev`、潛伏飆股 `quietA20` 09-29／30 缺** |
| 推薦名單快照 `data/recommendations/` | 最後一份 09-23；**09-24、09-29、09-30 缺**（09-24 的原因不明，排程日誌已隨 09-28 重建容器消失） |
| 處置股（櫃買） | ✅ 到 10-06（見 §2.3 的覆寫） |
| 處置股（證交所） | 停在 09-24。格式問題已不在（今天的回應沒有 `*`），下一次籌碼步驟的 14 日查詢窗會補到 |
| 當沖／借券 | 09-30 缺。籌碼步驟會自動補最近 10 個交易日的缺日，不用手動 |
| 紙上交易帳本 | 10-01 ~ 10-07 補跑時，選股事件晚於 3 天會被帳本拒記（D-142 §四，設計如此）——那幾天會以洞留在帳本裡，**不要繞過** |

查每天每張表列數的 SQL（`serving.trading_calendar` 這張表不存在，別查它）：

```sql
with d as (select generate_series('2026-09-22'::date, '2026-10-08'::date, '1 day')::date as date)
select to_char(d.date, 'MM-DD Dy') as day,
  (select count(*) from serving.prices_daily p where p.date = d.date) as px,
  (select count(*) from serving.margin_daily m where m.date = d.date) as margin,
  (select count(*) from serving.daytrade_daily t where t.date = d.date) as daytr,
  (select count(*) from serving.sbl_short_daily s where s.date = d.date) as sbl,
  (select count(distinct factor) from serving.factor_values f where f.date = d.date) as n_factor
from d order by d.date;
select to_char(date, 'MM-DD'), model_id, count(*) from serving.ml_scores
where date >= '2026-09-22' group by 1, 2 order by 1, 2;
```

`docker exec -i twquant-pg-1 psql -U twquant -d twquant < 檔案.sql`

## 2. 三個會漏資料的洞（09-24 起）

### 2.1 融資融券 19:00 還沒公布

證交所融資融券常在 19:00 之後才出來，排程當天那一次抓失敗。隔天的籌碼因子用 T−1 融資，於是整欄空，
兩條線的模型都拒絕打分。09-29、09-30 的「ML 推薦名單」「潛伏飆股打分」都是這樣 FAILED。

### 2.2 潛伏飆股要的五個因子被排除在每日物化之外

A20 用了第十二輪的五支盤整型態因子，它們在 `configs/serving.yaml` 的 `exclude_factors` 裡。
D-151 §十.3 寫了「模型要上線時再移除這五行」，T-318 上線潛伏飆股時漏了。
長歷史物化只做到 09-24，所以 09-25 之後每天打分都會失敗。

### 2.3 處置公告兩個來源都改了格式

- **證交所**：當天公告的那一列，公布日期前面多一個 `*`（`*115/09/29`），解析器拒收，整個籌碼步驟標 failed。
  隔天再查，同一列就沒有 `*` 了。
- **櫃買**：「處置原因」整年改成短版字樣（`因連續3個營業日達本中心作業要點第四條第一項第一款` → `連續3個營業日`）。
  守門（D-154 §二.6：內容變了不覆寫）整批拒寫，所以 09-29 起櫃買處置一列都沒進來。
  **已處理**：確認 469 列只有 `reason` 欄不同（日期、等級、撮合分鐘、措施全同），且沒有任何因子／策略／模型讀 `reason`，
  照守門訊息指定的程序執行 `scripts/backfill_chip_batch1.py alerts --market tpex --kind disposition --from-year 2026 --to-year 2026 --allow-changes`，
  覆寫 469 列、新增 21 列。覆寫前的整張表備份在 `data/backup/disposition_stocks_20261007_pre-tpex-reason.dump`。
  2025 年以前的列仍是長版字樣，沒動。

另外 `ml.lgbm_tail_up_20d`、`ml.pool_random` 每天物化都噴一段「須指定 model_id」的堆疊，是雜訊。

## 3. T-327（本分支的程式修正）——**未過全套閘門**

| 檔案 | 改了什麼 |
|---|---|
| `configs/serving.yaml` | 移除五支盤整因子的排除（修 §2.2）；加排除 `ml.lgbm_tail_up_20d`、`ml.pool_random`（理由同既有的 60 日版） |
| `configs/system.yaml`、`config.py` | `chip_daily.lake_catchup_tables: [margin_daily]` |
| `data/serving/direct_ingest.py` | `missing_days`（純函數）、`lake_gap_days`（從 PG 找最近 N 個交易日裡一列都沒有的日子） |
| `cli.py` | 籌碼步驟末尾呼叫 `_catchup_lake_days`：缺日逐日重抓進資料湖，同一輪的供應層步驟再鏡射進 PG（修 §2.1） |
| `data/sources/alerts.py` | 證交所處置的公布日期只多認一個開頭 `*`（修 §2.3 前半）；`**`、`#`、結尾 `*` 照舊 raise |
| 測試 | `test_chip_daily.py` 5 條、`test_chip_batch1.py` 2 條（含參數化共 9 個 case），全過、PG 那條有實跑沒跳過 |

已跑：`test_chip_daily.py`、`test_chip_batch1.py`、`test_serving_materialize.py`、`test_cloud.py` 全綠。
**沒跑**：`ruff`、`mypy`、全部 pytest——使用者在那一步喊停。接手要先跑 `python -m twquant check`（Windows 跑法見記憶
`twquant-run-gate-windows`），綠了才把 PR 從 draft 轉正。合併後照 WHERE-IS-EVERYTHING 的部署步驟重建 `api`、重啟 `api scheduler`。

**部署前**排程仍是舊程式：融資晚公布的隔天、以及每一天的潛伏飆股打分，都還是會失敗。手動補法就是 §4 的對應步驟。

## 4. 接手第一件事：補完九月尾巴與十月

**一次只跑一個重運算**（2026-09-10 併發把 Docker 弄崩、PG 整盤沒了）。全部指令都在主機上跑；`docker exec` 前加
`MSYS_NO_PATHCONV=1`（Git Bash 會把 `/app/...` 改寫成 Windows 路徑）。

```bash
cd D:/project/twquant/.claude/worktrees/d-159-06d55d
export MSYS_NO_PATHCONV=1 UV_LINK_MODE=copy PYTHONIOENCODING=utf-8 PYTHONUTF8=1
export TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant
M=/app/data/models
CONS=price.bb_squeeze_120,price.ma_tangle,price.vp_poc_dist_60,price.vp_support_hold_20,price.vp_near_share_60

# 一、九月尾巴
uv run --all-groups python scripts/materialize_long_history.py --from 2026-09-30 --to 2026-09-30 --factors "$CONS"
for d in 2026-09-29 2026-09-30; do
  docker exec twquant-api-1 python -m twquant ml score --date $d --model $M/lgbm_lw_dd_h60_20260225_1bd7c6a0 --pg
  docker exec twquant-api-1 python -m twquant ml score --date $d --model $M/lgbm_lw_rev_h15_20260225_9d4a149c --pg
done
for d in 2026-09-24 2026-09-29 2026-09-30; do
  docker exec twquant-api-1 python -m twquant ml export-recommendations --date $d --model $M/lgbm_lw_net_h20_20260225_83890036
done
docker exec twquant-api-1 python -m twquant ml score-quiet --from 2026-09-29 --to 2026-09-30

# 二、十月：一天跑完才跑下一天（券商分點一天約 10 分鐘，其餘步驟另計）
for d in 2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07; do
  docker exec twquant-api-1 python -m twquant pipeline --date $d
done

# 三、潛伏飆股（T-327 部署前，五因子不在每日物化裡）
uv run --all-groups python scripts/materialize_long_history.py --from 2026-10-01 --to 2026-10-07 --factors "$CONS"
docker exec twquant-api-1 python -m twquant ml score-quiet --from 2026-10-01 --to 2026-10-07
docker exec twquant-api-1 python -m twquant pick-board --date 2026-10-07

# 四、跑 §1 的 SQL 驗收，然後備份
docker exec twquant-pg-1 pg_dump -U twquant -d twquant -Fc > D:/project/twquant/data/backup/twquant_serving_20261008_post-catchup.dump
```

- 如果 T-327 已合併並部署，第二段的 pipeline 會自己物化五因子、補融資缺日、替潛伏飆股打分並重算看板，第三段整段可省略。
- **不要用容器裡的 `serving materialize --factors` 補這五支**：它沒有讀取下界，每個因子每天把整張行情表重讀一次，
  一天 6 分鐘、容器記憶體衝到 13~14 GB。`scripts/materialize_long_history.py` 有 cache＋since，約 4 秒一天。
- `materialize_long_history.py` 從這個 worktree 跑：**跑的時候不要改 `src/twquant` 的任何 .py**。
- 每天的 pipeline 結果逐行看：籌碼步驟應出現「margin_daily 缺日補」或沒有缺日；「ML 推薦名單」「潛伏飆股打分」部署前後的預期不同（見上）。

## 5. 最近一次訓練（D-162，2026-09-28）的結果

A20 重訓只用到 2026-02 的資料（`lgbm_quietA20_tu_h20_20260226_0ac86788`），考它沒看過的 2026-03-02 ~ 09-08。
這不是金庫：03~04 在 D-159 開發期內、05 之後是已開過的第一金庫，所以只能說「被推翻／未被推翻」，不能說「通過」。

- **選股力還在**：前 15 名 20 日內碰到 +20% 的比例 26.1%，同日安靜∩流動池 13.4%，1.95 倍。
- 1,000 萬、零股、期末持股按市價：

| 出場 | 期間報酬 | 最大回撤 | 每筆淨 | 平均持有 |
|---|---:|---:|---:|---:|
| 抱滿 21 天（S0） | **+43.7%** | −25.6% | +9.66% | 21.0 天 |
| ATR 移動停利（T-ATR） | +19.1% | −29.3% | +2.10% | 20.8 天 |
| ATR 停利＋固定 −15% 停損（T-ATR-F15） | +33.5% | −25.3% | +0.91% | 29.3 天 |
| 0050 買進持有（對照） | +38.8% | −15.4% | | |

- 照事前規則：**兩個停利臂都被推翻**（報酬都輸抱滿；T-ATR 回撤還更深）。開發期「T-ATR 勝抱滿」在沒看過的 6 個月方向反過來。
- 賺錢的是選股、不是出場規則。三臂仍照原樣留在第二保留期名單（D-109，2027-03-01 前不看績效）。

## 6. 這一輪踩到的坑（已寫進記憶 `twquant-data-quality-traps`）

1. **背景的 `docker exec` 被中止，容器裡的程序還在跑**。容器沒有 `ps`：用 `docker top twquant-api-1 -o pid,etime,rss,args` 看，
   殺的時候在容器內用 Python 掃 `/proc/*/cmdline` 再 `os.kill`。
2. **`docker stats` 的記憶體含檔案快取**，看起來 14 GB、程序本身可能只有 2.6 GB；要看 `docker top -o rss`。
3. 證交所處置 `*`、櫃買處置原因改字樣（§2.3）；守門擋下時先比對哪一欄不同，再決定要不要 `--allow-changes`。
4. 排程只跑「今天」：停機幾天後，價格、法人等資料湖表**不會**自己補（當沖／借券／公告會）。停機後一律照 §4 逐日補跑。

## 7. 完成紀錄（2026-10-08 凌晨，接手 session）

§4 一到四段全部做完，部署前的舊映像（09-28）上執行。逐項：

| 步驟 | 結果 |
|---|---|
| 九月尾巴：盤整五因子 09-30 | ✅ 用容器內 `serving materialize --factors … --trim-lookback` 補（5 分 33 秒；§4 不建議這條路，當時還沒讀到交接） |
| 九月尾巴：`lw_dd`／`lw_rev` 09-29／30 | ✅ 各 1,954／1,965 列。註：`lw_rev` 自 D-164 起不在 `production_aux_models`，十月本來就不打分，補這兩天無害 |
| 九月尾巴：潛伏飆股 09-29／30 | ✅ 491 列 |
| 九月尾巴：推薦快照 09-24／29／30 | ✅ 但**照 §4 原指令會被拒**（見下方第四個洞）；做法是先備份舊 parquet、`ml score --pg` 重打（分數逐位相同、只有產生時刻變）、再 `export-recommendations` |
| 十月：`pipeline --date` 10-01／02／05／06／07 | ✅ 逐日，每天 37~46 分鐘。各天都只有「潛伏飆股打分」FAILED（五因子，預期中）；10-01 另有證交所處置 `*115/10/07` 與紙上交易拒記（D-142 §四.4，未繞過） |
| 十月：盤整五因子 | ✅ `materialize_long_history.py` 5 天 2 分鐘 |
| 十月：潛伏飆股 | ✅ 1,207 列 |
| 看板 `pick-board --date 2026-10-07` | ✅ 23 分 50 秒（重播 05-01 起兩條線） |
| `broker_flow_daily` | ✅ 從 09-18 補到 10-07（只有雲端同步讀它，不影響選股；停在 09-18 早於這次停機） |
| 驗收 SQL（§1） | ✅ 09-29 ~ 10-07 每個交易日：行情、法人、融資、指數、券商分點、56 支因子、排序／風控／潛伏飆股三個模型分數都有。10-07 的當沖／借券是 0＝T+1 公布，下一次籌碼步驟自動補 |
| 備份 | ✅ `data/backup/twquant_serving_20261008_post-catchup.dump`（9.2 GB，21 分鐘；`pg_restore -l` 可讀出 29 張表的資料段） |

**紙上交易**：10-01 的換股事件晚 6 天補記被帳本拒絕（設計如此），所以 PT-E0 十月維持空手，11 月初換股才會建倉。

**第四個洞（D-169 待裁決）**：交接說「09-24 推薦快照的原因不明」——原因是**截點**。排程 19:00 開跑，排序模型約 19:17 打完分；
`export_scores` 要求推論產生時刻不早於當日 D-008 截點 20:00，否則拒絕匯出。10-08 照 §4 原指令補匯出時，
09-24／29／30 三天都以「推論產生時刻 19:17 早於該日訊號截點 20:00（D-008）」被拒——這是直接證據。
09-24 當天的排程應該就是死在這裡（`lw_dd` 那天有分數）；09-29／30 則更早撞到 `lw_dd` 缺融資特徵。
09-21～09-23 的分數檔時間是 21:02～22:12，落在 20:00 之後所以匯出成功（是當時 pipeline 較慢還是手動重跑，日誌已消失，未查證）。
**T-327 部署後這個洞仍在**；受影響的只有推薦快照（ML 選股頁）與洞悉分析，看板與選股讀 PG 的 `ml_scores`，不受影響。

**部署前的手動補法（10-08 19:00 排程跑完後）**：潛伏飆股照樣會因五因子失敗，補法同 §4 第三段（單日）：

```bash
uv run --all-groups python scripts/materialize_long_history.py --from 2026-10-08 --to 2026-10-08 --factors "$CONS"
docker exec twquant-api-1 python -m twquant ml score-quiet --from 2026-10-08 --to 2026-10-08
docker exec twquant-api-1 python -m twquant pick-board --date 2026-10-08
```

## 8. 2026-10-09 下午（同一 session 續做）——換 session 前的狀態

- **Docker 事故**：13:32 在 api 容器內手動 `ml export-recommendations --date 2026-10-08` 吃到 13.7 GB，Docker VM OOM、dockerd 斷線；
  使用者授權後以 `scratchpad/restart_docker.ps1` 重啟（停程序→`wsl --shutdown`→移走 `Dockerun`→啟動），PG 資料完好。登記 **T-328**（原因未查明，別再重試）。
  重啟後我自己的 `/api/meta` 探測迴圈把 API 執行緒池塞滿，`docker restart twquant-api-1` 後恢復；教訓在記憶 `twquant-deploy-layout`。
- **部署**：後端映像（10-08 04:04 建）就是 main 當時的版本、含 T-327；前端 9/28 映像已含 #113／#114，重建全部命中快取，所以當天沒有東西要部署。
- **T-329／T-330 已合併（#118、#120），尚未部署**：看板 HTML `data/reports/pick_board.html`（收盤、當日／近 6 日漲幅、整體表現、研究回測參考）。
  手動重畫：`TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant uv run --all-groups python -m twquant pick-board-html --out D:/project/twquant/data`。
  給使用者看的 artifact：https://claude.ai/artifact/9gGiVk1imPA5gBXGD4Kjka（第 3 版，資料到 10-08）。
- **下一個任務 T-331（未開）**：使用者指出重播 5 個月年化 +105% 沒意義——不足一年的區間不顯示年化／Sharpe／Calmar，只留期間報酬與最大回撤，
  改放逐年報酬表（已算過：大盤精選 2021~2025 年報酬 +33.6／−25.5／+54.4／+40.3／+22.0%，近 5 年年化 +25.3%；
  潛伏飆股 +32.0／−18.4／+37.3／−17.9／+12.3%，近 5 年年化 +8.6%；來源＝`pick_board.yaml` references 登記的長窗口包）。
- **其他待辦**：D-169 待裁決（建議排程改 20:05）；10-08 推薦快照仍缺（D-169＋T-328）；紙上交易帳本 10 月空手（D-142 設計）；
  潛伏飆股 10-12 開盤賣 14 檔、10-13 補新股。10-09 休市，排程 19:00 會跑但略過。
- worktree 已清：`C:/t/pbhtml`、`C:/t/docs-1008`（本分支合併後移除）。主 checkout `D:/project/twquant` 仍停在 PR #64，不要從它建映像。
