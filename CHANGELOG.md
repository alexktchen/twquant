# CHANGELOG

- 2026-07-06 T-018（部分完成）回測引擎：backtest/（costs/engine/metrics）+ CLI
  backtest + costs.yaml。next_open 執行、整數張、價格相依成本、漲跌停/參與率
  unfilled、會計恆等。真實回測 sample 策略連續期成本後 Sharpe 1.12、CAGR +8.6%、
  MDD -4.2%。閘門全綠（104 tests）。

- 2026-07-06 T-017（部分完成）策略框架：strategy/（config/universe/combine/
  overlays/build）+ CLI targets + sample_revenue_trust.yaml。因子→宇宙過濾→zscore
  組合→top_n→等權→寬度 overlay→單股上限，用 T-003 日曆排週頻。真實資料：sample
  策略 2026-06-30 產出 40 檔等權目標（總曝險 82%）。待接：ic_weighted、處置股排除、
  index overlay。閘門全綠（95 tests）。

- 2026-07-06 T-003 交易日曆：data/calendar.py（TradingCalendar + 由 prices_daily
  聯集推導 + 單檔持久化 + 來源核對）、CLI calendar。缺口偵測改以「開市日」為準、
  禁用 weekday()：TPEX 缺口 18→0（假日不再誤判）、TWSE 顯示 101 個真實 ban 缺口。
  閘門全綠（86 tests）。

- 2026-07-06 T-015 P0 籌碼 + 基本面因子：chip（投信/外資買超占量比、投信連買、
  融資變化散戶代理）、fundamental（月營收 YoY/加速，公布日 as-of 映射日頻 PEAD）、
  factors/builtins.py 一次註冊全因子。真實 IC + 對抗驗證（連續期 vs 全期）：
  revenue_yoy 最穩健（t≈+8），trust_net_10d 真實反向（t=-11，非缺口假象），
  trust_streak 全期數字有缺口灌水。閘門全綠（81 tests）。

- 2026-07-05 抓資料狀態介面：ingest_log（記錄每次擷取的成功/失敗/列數/細節，
  append-only 分片）；ingest/daily/backfill/ingest-revenue 全數記錄；`twquant status`
  CLI 顯示各來源涵蓋、平日缺口、最近失敗、最後執行；儀表板加「擷取狀態」面板。
  完全靠 CLI+排程器+儀表板運作，不需 LLM。實測 status 清楚顯示 TWSE 缺口 119 日。
- 2026-07-05 T-013 IC 檢驗 + research_log：factors/ic.py（RankIC/t-stat/多空價差）、
  reporting/research_log.py。真實驗證動能在台股 5 日顯著反轉、20 日無效。

- 2026-07-05 Docker infra（D-013）：Dockerfile（python-slim + uv）、docker-compose
  三服務（app / scheduler / dashboard）共用映像，DuckDB/Parquet 掛 volume。
  CLI 加 daily（容忍非交易日）與 scheduler（每日觸發）。Streamlit 儀表板 v1
  （讀倉儲顯示資料狀態與法人流向）。實測：容器讀共用 volume 資料、儀表板
  8501 服務、排程器正常。scheduler/dashboard 完整版見 T-023/T-024。

- 2026-07-05 T-011（部分）倉儲核心：清洗管線（TWSE raw→typed）、Parquet 分區
  資料湖（原子寫/分區級替換/PIT 20:00 截點）、DuckDB SQL 查詢引擎、CLI ingest/sql。
  **資料已可分析**：ingest 2026-06-30 存入三表共 3970 列，SQL 重現投信季底作帳。

- 2026-07-05 資料路徑 T-002→T-004→T-006→T-007：conventions（時區/例外/配置）、
  資料合約（14 表 pandera）、HTTP 基礎設施（滑動窗限流/重試/raw 落地）、TWSE rwd
  轉接器。**第一次實盤抓到台股資料**：2026-06-30 的日K/三大法人/融資券三張表，
  台積電收盤 2410、投信買賣超 -89,595 股等真實值已驗。全程閘門綠（35 tests）。

- 2026-07-05 T-001 專案骨架：pyproject(uv/hatchling)、src/twquant 佈局、
  `python -m twquant check` 開發閘門（ruff + format + mypy strict + pytest）、
  IO 邊界靜態測試、GitHub Actions CI。閘門正反雙向驗證通過。第一份程式碼。

- 2026-07-05 補跑完成（v1.2 規格修訂）：
  - 文獻驗證 76/76 全數完成（65 確認、11 細節修正、0 查無）。兩篇作者/年份
    張冠李戴已修正：「Momentum in Taiwan: seasonality matters!」為 Fu & Wood
    2010（非 Du/Huang/Liao）、Faber TAA 統計出自 2013 修訂版。
  - 規格對抗審查完成（3 視角 × 全文件）：23 high + 34 medium + 15 low 發現，
    經作者逐條核對後屬實。本輪修正 47 項（連動 DECISIONS D-004~D-012），
    11 項留待實作時設計選擇。完整記錄見 docs/research/REVIEW.md。
  - 主要修正：波動目標唯一歸屬（投組層條件式）、四態 kill-switch、PIT 20:00
    截點與多波次擷取、資料湖寫入協定（原子寫/版本化/並行）、漲跌停檔位精算、
    會計恆等式、投信持股比資料表、參與率分母統一、IC 兩階段門檻、文件優先權、
    portfolio_state 移出可重建層、build_targets 簽名統一、TASKS 依賴修正。
  - 完整性檢查 16 缺口納入 STRATEGY §5a：投信訊號 ETF 污染、結構斷點、
    Sleeve C 成本存活性、本地因子基準、待評估策略族（T-029/T-030）。

- 2026-07-05 新增 SPEC-008 洞悉層、MODEL-POLICY.md（模型分工）、docs/STRATEGY.md
 （策略設計書，整合 6 組研究代理 / 76 篇文獻）；LOOP.md 增為四閉環；
  TASKS.md 加模型分級欄與 T-026~T-028。文獻逐篇驗證與規格對抗審查因
  session 上限未完成，待補跑。
- 2026-07-04 規格階段：建立文件體系（CLAUDE.md、ARCHITECTURE.md、LOOP.md、
  SPEC-000~007、TASKS.md、DECISIONS.md）。尚無程式碼。
