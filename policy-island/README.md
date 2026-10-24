# 萬安政策島 TD（Taipei: Hold the Line）

非官方、網友自製的 3D 政策島塔防。島嶼、Q 版人物與場景的做法參考 puma.taipei 的「HEART 政策島」互動頁，
全部重新實作；玩法改成「島上的居民和登入的英雄一起擋下一波波議題攻擊，而且每一波一定守得住」。

## 玩法
- **12 座島 = 台北 12 個行政區**，依實際地理相對位置排列（上北下南、左西右東），市政府在信義區。每區有一個象徵地標（溫泉、故宮、龍山寺、台北車站、101、貓空纜車……）。
- **政策散落在各島**：每筆政策是一棟可點擊的建築，點了會開政策卡。有指定行政區的放在該區，沒指定的依代號雜湊隨機落島（每個人看到的位置一致）。
- **一場約 3 分鐘**：依當天議題，青鳥、青蛙、鸚鵡、毛蟲、變色龍、鍵盤鼠從淡水河、關渡、基隆河、景美溪登陸，沿步道與橋走向市政府。
- **居民會自己防守**：每區 5 位居民平常在步道散步，有敵人就走過去圍住勸退。
- **英雄**：登入者選一種職業，點島嶼就走過去，自動攻擊射程內敵人；點敵人重擊；Q 放大招。
- **一定守得下來**：第 158 秒起每 1.5 秒「全民護城」，176 秒清場；滿意度低於 40% 也會提前啟動；滿意度有下限。
- 每場結束全體「已阻擋 N 波攻擊」+1。**大選前一晚**（`election.date` 前一天 00:00，台北時間）起自動進入總攻擊議題。
- 全版模式：3D 鋪滿視窗，右上「全螢幕」鈕。

## 檔案
| 檔案 | 用途 |
|---|---|
| `index.html` | 獨立網頁（丟任何靜態主機即可；three.js 已 vendor 進 `vendor/`） |
| `css/ui.css` | 全版 UI |
| `js/config.js` | **所有可調參數**：行政區島嶼與地標、政策、動物、英雄、議題排程、大選日、保證勝利時間點 |
| `js/store.js` | 共享狀態轉接器（見下） |
| `js/env.js` | 天空、水、台北盆地地形、雲、晝夜 |
| `js/islands.js` | 六邊形島、步道環、島側、橋、行人路網（Dijkstra）、建築槽位 |
| `js/buildings.js` | 實例化零件庫、地標與政策建築迷你模型 |
| `js/people.js` | Q 版居民與英雄（實例化、走路動畫） |
| `js/animals.js` | 攻擊方動物 |
| `js/game.js` | 一場的模擬：敵人、居民 AI、英雄、保證守住 |
| `js/ui.js` | 大廳、HUD、政策卡、後台、結算 |
| `js/main.js` | 進入點、相機、點擊、政策落島 |
| `scripts/build-artifact.js` | 產生 claude.ai artifact 版頁面 |

## 後台：新增／編輯政策與議題
artifact 版大廳底部「後台：政策與議題（編輯者）」只有 artifact 編輯者看得到：
- **政策**：選一筆編輯或新增；可指定行政區或「不指定（隨機落島）」；外型從既有模型挑。存進共享資料庫 `policies/<id>`，所有人的島上立刻多一棟。
- **議題攻擊**：選一筆編輯或新增；動物組合、登陸方向、魔王、口號、起始日。存進 `issues/<id>`。
不走 UI 也可以直接改 `js/config.js` 的 `policies` / `issues`。同代號時資料庫的覆蓋 config 的。

## 共享狀態（store.js）
| 環境 | 行為 |
|---|---|
| claude.ai artifact | `db` + `user` 能力。`heroes/<uid>` 每人一份（只有本人能寫），總數 = 全部加總；`issues/<id>`、`policies/<id>` 由編輯者寫入。 |
| 其他（本機、靜態站） | 退回 `localStorage`，只記本機戰績；政策與議題只用 config。 |

拆成正式站時把 `store.js` 的 remote 分支換成 Supabase / Firebase（介面：`init / onGlobal / onLeaderboard / onIssues / onPolicies / saveHero / recordRound / addIssue / addPolicy`），其餘不用動。

## 本機執行
```bash
python3 -m http.server 8080   # 在這個資料夾；然後開 http://localhost:8080
```
只有 Noto Sans TC 字型需要網路（Google Fonts）。

## 產生 artifact 版
```bash
node scripts/build-artifact.js /tmp/artifact.html
```
把輸出當 artifact 頁面，`css/`、`js/`、`vendor/` 以附檔一起發布。

## 免責
政策摘要整理自公開報導、僅供遊戲參考，細節以官方資訊為準。地標與建築是象徵性迷你模型；議題、數值、動物與路線純屬遊戲設定，與任何真實人物或團體無關。
