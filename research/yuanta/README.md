# 元大期貨交易 API

使用者的期貨戶在元大期貨（2026-11-12：「我要用元大 富邦不要了」）。這裡放接元大交易 API 的程式與筆記。
**目前只有連線測試，沒有任何送單的程式。**

## 現況（2026-11-12）

| 項目 | 狀態 |
|---|---|
| 64 位元元件註冊 | 完成（`Yuanta.YuantaOrdCtrl.64`） |
| `yuanta_check.py --component-only` | 通過（元件建立成功） |
| 登入、查權益數、查未平倉 | **還沒成功跑過**——使用者的帳號 2026-11-13 才生效 |
| 測試主機 `apitest.yuantafutures.com.tw:80` | 2026-11-12 晚上連不到（原因不明，要問元大） |
| 正式主機 `api.yuantafutures.com.tw` 80／443 | 連接埠通 |

## 官方資料

- 下載頁：<https://www.yuantafutures.com.tw/ytf/easywin/api/download.html>
  （「交易API元件及說明文件」1.6.1.3，2022-08-05；另有 Python 交易範例）
- 申請：官網「線上服務」<https://www.yuantafutures.com.tw/apisigning>，登入後簽
  「API風險預告暨申請使用聲明書」，核准開通後才能用（登入後的畫面沒看過，簽署位置是推斷）。
- 客服 (02)2326-1000。元大不提供程式開發教學。
- 元件與官方文件不放進 repo，要用時從下載頁重新抓。

## 這套 API 是什麼

- Windows 專用的 ActiveX 元件（`YuantaOrd64.ocx`），要用系統管理員執行
  `C:\Yuanta\API_x64\install_YTFutOrdAP64.bat` 註冊。只能在使用者的 Windows 電腦上跑，雲端排程用不了。
- Python 透過 `comtypes` 呼叫；Python 位元數要跟元件一樣。元件要掛在一個視窗上才能建立，
  `yuanta_check.py` 用隱藏視窗＋ATL，不需要官方範例的 wxPython。
- 用歸戶身分證字號＋電子交易密碼登入，下單另需憑證。
- 只管下單與帳務；行情是另一包「國內行情API」。沒有現股下單。

| 功能 | 元件的函式 | 回報事件 |
|---|---|---|
| 登入／登出 | `SetFutOrdConnection`、`DoLogout` | `OnLogonS` |
| 國內期權下單、減量、刪單、改價 | `SendOrderF` | `OnOrdResult`、`OnOrdRptF`（委託）、`OnOrdMatF`（成交） |
| 國外期貨下單、刪單 | `RfSendOrder` | `OnRfOrdResult`、`OnRfOrdRptRF`、`OnRfOrdMatRF` |
| 查當日委託／成交 | `ReportQuery`、`DealQuery` | `OnReportQuery`、`OnDealQuery` |
| 未平倉、部位、權益數 | `UserDefinsFunc`（FA001／FA002／FA009／RA003／FA003） | `OnUserDefinsFuncResult` |

國內委託：市價、限價、一定範圍市價；ROD／IOC／FOK；新倉、平倉、當沖或自動。
商品代碼例：期貨 `TXFD9`、價差 `TXFD9/E9`、選擇權 `TXO04500E9`。

## 連線測試怎麼跑

程式與環境放在 repo 外（`C:\t\yuanta`，Python 3.12＋comtypes），在 repo 外執行。
帳密只放 `D:\project\twquant\.env`：`YUANTA_ID`、`YUANTA_PASSWORD`。

```bash
# 只檢查元件（不登入）
C:/t/yuanta/.venv/Scripts/python.exe C:/t/yuanta/yuanta_check.py --component-only
# 登入（預設測試環境）
C:/t/yuanta/.venv/Scripts/python.exe C:/t/yuanta/yuanta_check.py --env D:/project/twquant/.env
# 正式環境（只登入、查詢、登出）
C:/t/yuanta/.venv/Scripts/python.exe C:/t/yuanta/yuanta_check.py --env D:/project/twquant/.env --host api.yuantafutures.com.tw --port 443
```

登入與之後的真實送單都由使用者自己啟動。

## 陷阱與還不確定的事

- **元件預設會把往來封包寫成檔案**（文件：一般程式寫在執行檔路徑下），可能含身分證字號。
  程式登入前先 `SetLog(False)`，而且一律在 repo 外執行（富邦 SDK 的 log 曾被推上公開 repo）。
- 卡在「呼叫回傳：3 線路連線中」＝連不到主機，先測連接埠。
- 權益數查詢（FA003）的參數是照文件欄位表拼的，文件只有未平倉（FA001）的範例，還沒驗證。
- 測試環境收不收正式帳密，文件沒寫。
- 寫下單程式之前要先讓測試環境通，不拿正式帳戶試單。
- repo 的券商介面 `src/twquant/execution/broker.py` 只描述現股整張限價單，
  沒有合約月份、新平倉、市價單、改單刪單；要接元大得先擴充（另開任務）。
- 小台的價格規則九年重測全部不及格（`research/momentum_scalping`）。
  下單 API 是幫使用者送他自己決定的單，不是讓那些規則自動下真單。
