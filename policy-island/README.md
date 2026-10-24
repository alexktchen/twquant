# 萬安政策島 TD（POC）

非官方、網友自製的 3D 政策島塔防。靈感來自 puma.taipei 的「HEART 政策島」互動頁；
這裡把它變成「每個登入的人都是英雄，一起擋下一波波議題攻擊」的 3 分鐘小遊戲。

**這個資料夾與 twquant 的量化程式無關**，只是暫時放在同一個 repo；POC 結束後會整個拆出去。
不受 `CLAUDE.md` 的 Python 閘門約束（沒有 Python，`python -m twquant check` 不會掃到它）。

## 玩法摘要
- 一場約 **3 分鐘**（`config.js → round.seconds`）。敵人依「議題」從四條河道撲向信義區市政府。
- 玩家 = **登入的英雄**（五種職業），站在市政府旁自動攻擊；點敵人可重擊；大招「台北人站出來」。
- 用預算蓋政策塔（TPASS、社宅、大巨蛋、托育、敬老卡、台北通）。
- **一定守得下來**：第 158 秒起每 1.5 秒觸發「全民護城」，176 秒清場；滿意度有下限。
- 每場結束 = 全體「已阻擋 N 波綠營攻擊」+1，並顯示「我們已經阻擋了 N 波」。
- **大選前一晚**（`election.date` 的前一天 00:00，台北時間）起自動進入「總攻擊」議題：四面八方、所有動物、兩隻魔王。之後可隨時重演。

## 檔案
| 檔案 | 用途 |
|---|---|
| `index.html` | 獨立網頁（可直接丟任何靜態主機） |
| `css/ui.css` | 版面 |
| `js/config.js` | **所有可調參數**：一場長度、保證勝利時間點、地圖、動物、英雄、塔、議題排程、大選日 |
| `js/store.js` | 共享狀態轉接器（見下） |
| `js/world.js` | three.js 場景：島、路線、市政府、塔、英雄、動物外型、特效 |
| `js/game.js` | 一場的模擬 |
| `js/ui.js` | 大廳、HUD、側欄、結算 |
| `scripts/build-artifact.js` | 產生 claude.ai artifact 版頁面 |

## 新增議題攻擊
兩種方式，效果相同：
1. 改 `js/config.js` 的 `issues`，加一筆 `{ id, title, tag, startsAt, intensity, routes, boss, mix, chants, desc }`。
2. 在 artifact 版大廳底部「新增議題攻擊（編輯者）」表單填入（只有 artifact 編輯者看得到），資料進共享資料庫的 `issues` 集合，不用重新部署。

`startsAt` 最新且 ≤ 今天的議題會被標成「今日議題」並預設選取；玩家仍可手動選其他議題重演。

## 新增攻擊方動物 / 英雄職業
- 動物：`config.js → animals` 加一筆，`model` 選 `bird / frog / worm / lizard / mouse` 之一（外型在 `world.js → makeAnimal`）。
- 英雄：`config.js → heroes` 加一筆，`style` 選 `ball / bus / water / zap`。

## 共享狀態（store.js）
| 環境 | 行為 |
|---|---|
| claude.ai artifact | 用 `db` + `user` 能力。`heroes/<uid>` 每人一份（只有本人能寫），總數 = 全部加總；`issues/<id>` 由編輯者寫入。 |
| 其他（本機、拆出去的靜態站） | 退回 `localStorage`，只記本機戰績。 |

拆出去時只要把 `store.js` 的 remote 分支換成 Supabase / Firebase（介面：`init / onGlobal / onLeaderboard / onIssues / saveHero / recordRound / addIssue`），其餘檔案不用動。

## 本機執行
```bash
cd policy-island && python3 -m http.server 8080   # 然後開 http://localhost:8080
```
需要網路載入 three.js r128（cdnjs）與 Noto Sans TC（Google Fonts）。

## 產生 artifact 版
```bash
node policy-island/scripts/build-artifact.js /tmp/artifact.html
```
把輸出當 artifact 頁面，`css/`、`js/` 以附檔一起發布。
