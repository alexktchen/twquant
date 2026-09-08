# SPEC-014 — 研究平台與推薦追溯

版本 1.0，2026-09-06。產品、畫面與操作旅程見 [PRD-003](../PRD-003-platform-console.md)，ML／洞悉回饋見 [PRD-002](../PRD-002-insight-ml-recommendation-platform.md)。本規格定義第一版自用平台的實作契約；工單與進度以 [TASKS](../../TASKS.md) 為準。

## 1. 邊界與交付順序

依 D-080 採分期讀取平台。前端使用 React、Vite、TypeScript、Lightweight Charts 5.2；後端沿用 FastAPI、reporting 與既有資料供應層。先讀取現有產物及明確標示研究模式的候選，不變更 D-066 的實驗、模型發布資格或交易流程。
新頁面不得使用 frontend/data.json 代替缺席的模型、ML 推薦、訓練、回測或洞悉。尚未接入、真正零筆與讀取失敗三種狀態分開。既有個股離線快照仍有明顯標示；遷移不清除自選股。
工單 T-200～T-215 由同一工作流依依賴順序領取，一次只將一張實作工單設 in_progress。每張工單先寫對應 AC 測試，再最小實作，通過完整 gate 才 done；任何外部資料／研究阻礙記錄到工單，不把示意資料當成完成。

## 2. 平台路由、殼層與狀態（T-201）

為兼容現有單檔打包與靜態部署，第一版使用 hash 深連結，例如 `#/stocks/0050?date=2026-08-24&run=run-a`；PRD 的路徑在 hash 內保持一致。API 路徑維持 `/api`，不需改反向代理。
路由：overview、recommendations、watchlists、stocks/:sid、industries、insights、models、backtests、operations；models／backtests 可接單一 artifact ID。股票代號保持字串，URI 安全編碼。未知路由顯示找不到頁面與回首頁入口，禁止靜默替換成另一股票。
URL 可保存 date、model、run、bucket、sort、page、q、tab、mode；瀏覽器返回／前進及重新整理恢復上下文。不以本機時區將交易日轉成 UTC。第一版日期與模型由資料頁選擇，殼層顯示當前查詢值；缺席為「最新可得」，不偽裝成今天。
Shell 負責分組側欄、手機導覽、頁首／breadcrumb、連線與資料日期。所有導航用可鍵盤操作的連結，當前項有 aria-current。新頁面獨立模組，既有 StockFactorsPage 與 Ingest 透過插槽接入。

- AC-201-1：直接開個股／模型／回測 URL 可解析身分，0050 不丟前導零；重新整理仍一致，非法路徑可見錯誤。
- AC-201-2：跳轉、返回、前進保留查詢值與 hash 路徑；不串改另一個頁面的日期／模型。
- AC-201-3：375px／1280px 無頁面橫向溢出，導覽可用鍵盤；新模組無資料時不顯示假績效或假推薦。

## 3. 研究產物索引（T-202～T-204）

讀取邊界在 reporting，API 只取投影。根路徑由服務端配置注入，客戶端只能傳 artifact ID，不能傳檔案路徑。首版不在 GET 觸發訓練、推論或回測。
模型讀取現有 model meta.json；回測讀取 result_bundle。索引 ID 由類型與相對路徑穩定導出，原檔不改寫；有保險庫內容的模型欄位不得出現在讀取投影或錯誤回應。訓練詳細事件未記錄時回傳 unavailable，而非偽造成功歷程。列出 archive／rejected 產物時標記原身分；不列作正式可發布模型或有效績效。
契約：`GET /api/platform/models`、`/models/:id`、`/backtests`、`/backtests/:id`；清單為 `{items, source, issues}`，空目錄 items=[]，損毀檔在 issues 中明示無效項，不靜默丟棄。詳情使用已驗證的 ID 白名單，缺檔 404，格式錯誤 422；資料不可讀 503。回測清單只取摘要，曲線／交易明細在詳情讀取。
模型投影包含 model_id、train_end、features、horizon_days、label、版本指紋及已允許的開發 IC；started_at／ended_at、execution_status 與發布資格未知則 null／unreviewed。DSR trials 不因匯入或查看頁面而增加。
回測投影保留 config、成本／資金、期間、fingerprint、metrics、equity、ledger、unfilled、holdings。不同口徑時禁止 UI 宣告優勝；不在前端自行再計算正式績效。

- AC-202-1：模型與 result_bundle fixture 可被索引，連讀兩次 ID／內容一致且不改原檔；舊欄位缺席為未知。
- AC-202-2：損毀、拒絕、歸檔和保險庫欄位有明確處理；不外洩 vault 指標／任意檔案內容。
- AC-203-1：清單、詳情、404／422／503 正確；路徑穿越與非白名單 ID 被拒絕，GET 不觸發運算。
- AC-204-1：模型、回測清單與詳情接真實 API；選中身分和 URL 一致，loading／error／empty 可辨識。
- AC-204-2：回測淨值使用 Lightweight Charts；日期與數字對得上結果包；舊訓練進度明示未記錄。

## 4. 推薦快照與每日清單（T-205～T-206）

推薦讀取不得拿綜合因子排名冒充 ML。每筆 run 必須有 run_id、model_id、as_of、generated_at、published_at（尚未发布可 null）、mode、artifact_fingerprint、candidates。時間均 tz-aware Asia/Taipei。
候選 stock_id 為字串；rank、score、bucket、reason_ref、insight_run_id、data_status 分開。保留全部候選及原始排名，未分析是 pending。正式推薦資格沒有依據時 mode=research；重建歷史 mode=reconstructed，不算向前發布成效。
首版經受控 CLI 匯入已存在的合約快照並原子保存；相同 run_id／相同 payload 冪等，衝突內容拒絕覆寫。獨立後續工單才把日頻推論出口接到快照寫入，不在讀取頁偷偷運算。
`GET /api/platform/recommendations?date=&model=&run=` 回傳符合的 run 列表與候選；沒當日結果回空，不退到最近一期卻保留今日標題。`GET /api/platform/stocks/:sid/recommendations` 返回該股事件。所有篩選先於分頁。

- AC-205-1：合約拒絕 naive 時間、重複股票／排名、前視事件與錯誤模式；同 run 重匯冪等、不同內容衝突可查。
- AC-205-2：未發布／未核准不能變成正式推薦；原始 ML 分數、洞悉分類及未分析候選全部保留。
- AC-206-1：首頁與每日清單使用相同 run，日期／模型／分類／搜尋篩選可還原；可點到個股及來源模型。
- AC-206-2：有資料、合法零股、未產生、錯誤／過期各自顯示；不使用 demo 或綜合排名補位。

## 5. 個股研究與推薦圖層（T-207～T-208）

個股研究從選股工具中分離，首屏是股票資訊、K 線與推薦事件；籌碼／基本面／因子用頁籤減少長頁。既有因子選股器與 StrategyPools 留在明標「條件選股」入口，不能改稱 ML。
行情身分由 sid＋日期上下文組成；切換時不顯示舊股資料，API 失敗明示，不能以離線資料冒充連線成功。
沿用 CandleChart 增加可選推薦 marker 輸入，v5 createSeriesMarkers；事件詳情來自推薦快照。日期映射、盤後、休市／停牌、週 K、同日多事件及還原口徑依 PRD-003 §14。事件與 K 線無交集時保留清單且不貼到前一交易日。

- AC-207-1：從清單進入正確 sid，返回保留篩選；K 線優先、資料頁籤可操作且資訊不丟失。
- AC-207-2：切股／切日與失敗請求沒有舊行情殘影；以原始字串保存代號。
- AC-208-1：推薦箭頭對應日期／run，無推薦不造標記；同日多事件可展開，重複 ID 不重畫。
- AC-208-2：盤後／缺交易日／週 K 映射符合 §14；點日期定位、標記開關、資料表、鍵盤操作及圖表清理通過。

## 6. 產業、洞悉、自選與營運（T-209～T-212）

產業先接既有 sector-flows：清楚顯示 API 提供的窗口、資料日與涵蓋股票數，不捏造成長／消息結論。stock_meta 只有當前分類時標明當前分類，不能宣稱歷史 PIT 分類。
洞悉以結構化證據包讀取：evidence_id、來源標題／URL、published_at／available_at／captured_at、stock_id／industry、方向／缺口、reason_ref；分析同時保存 prompt／schema／模型版本與 as_of。引用不存在或晚於截點拒絕發布分析；原文是資料，不作工具指令。LLM 離線只顯示證據與缺席原因。
自選先保持本機 localStorage 鍵相容，加入研究筆記與推薦 run 參照；讀寫失敗明示，損毀資料不能默默刪除。跨帳號的伺服器寫入與公開會員是後續版本，不能假稱已完成。
營運頁重整資料／缺口／工作分頁，沿用既有 ingestion 白名單與單工規則，不添加新訓練／下單按鈕。poll 更新不重設使用者頁籤。

- AC-209-1：產業表格／圖表與 API 一致，缺資料可辨識，點產業能查看相應股票或候選。
- AC-210-1：洞悉引用與截點驗證、支持／反對證據及缺口可查；離線／不存在不得生成假分析。
- AC-211-1：舊自選可讀、加入移除／筆記持久化、0050 保留；儲存失敗與壞資料顯式提示。
- AC-212-1：舊資料操作仍可達、白名單／併發保留；工作輪詢不重設頁籤，不新增高權限工作。

## 7. 後續閉環與整合驗收（T-213～T-215）

T-213 將既有日頻 ML 推論結果輸出合約快照，先保存研究候選；必須有真實 model artifact、PitView 截點、發布模式與冪等依據。不能為填畫面另啟一個研究試驗，不能因訓練成功而晉級。
T-214 保存洞悉結構化特徵、候選全體與成熟 outcome 的資料字典／匯出契約；未成熟 target=null，排除股仍追蹤，標籤定義及成本引用既有 SPEC。此工單不啟動新 ML 訓練，研究授權與資料成熟後另領工單。
T-215 用固定 HTTP fixtures 驗證每日選股→個股 K 線／推薦日→來源模型／回測→追蹤及返回，桌面／手機／鍵盤全流程。後端 gate 不替代前端 typecheck、build、路由單測及瀏覽器驗證。部署／對外公開另有工單與環境驗收。

- AC-213-1：相同推論 run 僅一份快照；日期、model_id 與原始推論逐列一致；失敗不發布半成品。
- AC-214-1：回訓匯出保留 PIT 特徵身分、全部候選及 null 未成熟 target；不以 LLM 分數當真實標籤。
- AC-215-1：固定資料完整旅程與無資料／故障場景通過；逐頁有真實來源或明確未產生狀態；測試不連網。

## 8. 逐張工單的證據

前端單測使用 Node test runner（Node 22 的 strip-types）測試純路由／事件映射；介面變更另跑 `npm run build` 和固定資料瀏覽器驗證。Python 模組依 repo AC 命名與完整 `python -m twquant check`。每張任務記錄紅測試、綠測試、檔案範圍及未完成限制。若一張任務非測試 diff 超過 400 行，先拆工單再實作。
