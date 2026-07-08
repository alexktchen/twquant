"""可插拔擷取層（D-017）：來源（SourcePort）與儲存（IngestSink）各自可獨立抽換。

ports：兩個 Protocol；registry：來源註冊表（比照 factors/registry）；orchestrator：
以 IngestSink 落地 SourcePort 的資料並記 log；builtins：匯入即註冊內建來源。
"""
