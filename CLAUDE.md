# CLAUDE.md — AI 實作規則書

你（AI 代理，不論哪個模型）在這個 repo 工作時必須遵守本文件。
本文件的設計目標：即使是較弱的模型、較小的 context，只要照規則走，
產出就會被自動化閘門攔住錯誤，系統品質不依賴模型聰明程度。

## 0. 閱讀順序

1. 本文件（CLAUDE.md）
2. [docs/specs/SPEC-000-conventions.md](docs/specs/SPEC-000-conventions.md) — 全域慣例
3. 你要做的任務在 [TASKS.md](TASKS.md) 指到的那一份 SPEC
4. 只有需要跨模組時才讀 [ARCHITECTURE.md](ARCHITECTURE.md)

一次只做一個任務。不要為了「順手」重構任務範圍外的程式碼。

## 1. 黃金規則（違反任何一條 = 該次變更全部作廢）

1. **前視偏誤**：因子與策略只能透過 `PitView` 讀資料。任何直接讀 parquet、
   直接查 DuckDB、或把未來資料傳進因子函數的程式碼，一律不合格。
2. **時區**：所有 timestamp 一律 tz-aware `Asia/Taipei`。naive datetime 禁止。
3. **單位**：成交量單位是「股」不是「張」；金額是 TWD；報酬率是小數（0.05 = 5%）。
   跨單位轉換只能發生在 data 清洗層，並在 schema 註明。
4. **股票代號**：一律字串，保留前導零（"0050"）。禁止轉 int。
5. **禁止靜默處理**：不准 `except: pass`、不准自動填 NaN 成 0、不准 drop 掉
   驗證失敗的列繼續跑。資料有問題就 raise，讓品質閘門處理。
6. **golden 檔案不可改**：`tests/golden/` 下的任何檔案是驗收基準。
   測試不過，改的是你的程式，不是基準。若你確信基準錯了，走 DECISIONS.md 流程。
7. **不准為了讓測試通過而放寬測試**：改 assert 容忍度、刪測試、加 skip 都算。
   同樣走 DECISIONS.md。
8. **參數放 configs/**：`.py` 檔裡出現魔術數字（回看天數、閾值、費率）即不合格，
   一律從配置注入。
9. **秘密**：API token 只能放 `.env`（已 gitignore）。任何程式碼、配置、測試、
   文件中出現真實 token 即嚴重違規。
10. **IO 邊界**：`factors/ strategy/ backtest/ portfolio/` 內禁止 import
    `requests`、禁止開網路連線、禁止寫檔（回傳值由呼叫端落地）。

## 2. 開發閉環（每個任務都走這個迴圈）

```
┌─► (1) 領任務：TASKS.md 挑最小編號的可做任務，狀態改 in_progress
│   (2) 讀規格：任務指到的 SPEC 章節與驗收條件（AC-x）
│   (3) 先寫測試：每條 AC 對應至少一個測試，先確認測試會失敗
│   (4) 實作：最小可過測試的實作，遵守黃金規則
│   (5) 跑閘門：python -m twquant check（= ruff + mypy + pytest 全套）
│   (6) 自我審查：對照 §3 完成定義逐條打勾，寫進任務的完成註記
│   (7) 收尾：更新 TASKS.md 狀態 + CHANGELOG.md 一行 + 若介面有變更同步 SPEC
└───(8) 閘門全綠才算完成；任何一項紅 → 回 (4)，最多三輪；三輪不過 → 停下，
        把卡住的原因寫進 DECISIONS.md 的「待裁決」，換下一個不相依的任務
```

閘門指令（實作於 T-001，之後不准繞過）：
```bash
python -m twquant check          # 全套：ruff check + ruff format --check + mypy + pytest
python -m twquant check --fast   # 只跑受影響模組的測試（開發中用）
```

## 3. 完成定義（Definition of Done）

一個任務完成的充要條件，全部要能拿出證據（指令輸出）：

- [ ] 該任務 SPEC 的每一條 AC 都有對應測試，且測試名稱含 AC 編號（如 `test_ac3_...`）
- [ ] `python -m twquant check` 全綠
- [ ] 新增的公開函數/類別都有 type hints 與一行 docstring
- [ ] 沒有動到任務範圍外的檔案（`git diff --stat` 檢查）
- [ ] 參數都在 configs/ 或函數參數，無魔術數字
- [ ] TASKS.md 該任務標記 done 並附閘門輸出摘要
- [ ] CHANGELOG.md 加一行（日期、任務編號、一句話）

## 4. 測試規則

- **測試金字塔**：單元測試（純函數）為主；每個模組一個整合測試；
  端到端 smoke test 只有一條（固定 fixture 資料 → 回測 → 指標與 golden 比對）。
- **屬性測試（必寫，這是防弱模型的主要護欄）**：
  - 反前視性質：對任何因子 f，`f(data[:t])` 必須等於 `f(data)[:t]`。
    框架提供 `assert_no_lookahead(factor_fn)` 工具（T-012），每個新因子的
    測試檔必須呼叫它。
  - 冪等性質：同一輸入跑兩次，輸出 bit-identical（決定性；隨機需固定 seed）。
  - 不變量：權重和 ≤ 1 + 1e-9、個股權重 ≤ 上限、成交量參與率 ≤ 配置值。
- **golden fixtures 分兩類（REVIEW H14）**：
  （1）**輸入資料切片**（約 30 檔 × 2 年，含除息/漲停/處置/月營收/減資案例）：
  由 T-005 一次性生成後凍結，黃金規則 6 禁止修改。
  （2）**引擎預期輸出**（IC 報告的 RankIC、回測指標）：其生成需要對應引擎
  （T-013、T-018）才存在，故在該引擎任務**首次通過複審後才凍結**。追加預期輸出
  不算違反「不可改」，但必須在任務完成註記留下生成指令與複審紀錄。
- 測試不得連網。來源轉接器的測試用錄好的 HTTP fixture（`tests/fixtures/http/`）。
- **IO 邊界靜態閘門（REVIEW H8）**：`check` 套件含一條靜態測試，驗證
  `factors/ strategy/ backtest/ portfolio/ insight/` 內無 `open(`、`write_parquet`、
  `import requests`、`import anthropic`（洞悉層例外，見 SPEC-008）。落地由
  `reporting/`、資料層或 CLI 負責。這把黃金規則 10 從「靠審查」升級為「機器可攔」。

## 5. 程式風格

- 模組結構、命名、docstring 跟著既有程式碼走；本 repo 尚無程式碼時，跟 SPEC 範例走。
- 函數優先於 class；class 只用在有狀態的邊界物件（Warehouse、Broker）。
- polars 優先，鏈式寫法，避免 `.to_pandas()` 除非介接外部庫。
- 錯誤訊息要含上下文：壞在哪張表、哪個日期、哪檔股票、期望 vs 實際。
- 註解只寫「程式碼本身無法表達的約束」（如「TWSE 這個欄位在 2020-03 前單位是千元」）。

## 6. 規格模糊時（Escalation）

猜測是被禁止的。遇到 SPEC 沒定義、或兩份文件矛盾：

1. 在 DECISIONS.md 的「待裁決」段落新增一條：問題、你建議的選項 A/B、影響範圍
2. 如果有不相依的其他任務 → 先去做那個
3. 如果被完全卡死 → 停止並在回報中明確說「被 D-xxx 卡住」

裁決後（由使用者或被授權的 AI 決定），把結論搬到「已裁決」段落，才能實作。

## 7. 給小 context 模型的工作法

- 一次只載入：CLAUDE.md + SPEC-000 + 當前任務的 SPEC 章節。不要全部讀。
- 改動前先跑 `python -m twquant check --fast` 確認基線是綠的；
  基線就是紅的 → 不是你的問題，記進 DECISIONS.md 待裁決，別動手修別人的模組。
- 每改 1-2 個檔案就跑一次 fast 閘門，小步前進。
- 禁止大型重寫：單一任務 diff 超過 400 行（不含測試與 fixture）就該回頭看
  是不是超出任務範圍。

## 8. 禁止事項總表（速查）

| 禁止 | 正確做法 |
|---|---|
| 直接讀 data/ 下的 parquet | 透過 `Warehouse.view(as_of)` |
| naive datetime / UTC 存台股日期 | tz-aware Asia/Taipei |
| 把「張」當「股」 | 清洗層統一為股，schema 註明 |
| `except Exception: pass` | 讓它 raise，品質閘門會接住 |
| 改 tests/golden/ | 走 DECISIONS.md |
| 放寬 assert / skip 測試 | 走 DECISIONS.md |
| .py 裡寫死參數 | configs/ + pydantic 配置 |
| 因子函數裡 fetch 網路資料 | 因子是純函數，資料先進倉儲 |
| 一個任務裡順手重構別的模組 | 開新任務 |
| commit token / 帳密 | .env（gitignore） |

## 9. 專案長期記憶

- **台股 ML 選股研究**：結論、已排除方向、文獻與共編偏好見
  [memory/projects/twquant-ml-stock-selection.md](memory/projects/twquant-ml-stock-selection.md)。
- 投資者版實驗結果統一維護在 `data/reports/台股ML選股實驗總覽.html`；使用白話、保留失敗結果，
  並讓每個數字可追溯。新想法須明確標示尚未回測。
