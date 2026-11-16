"""打包成 exe 的進入點（A-023）：跟 `python -m autotrade` 同一個指令列。"""

from __future__ import annotations

import multiprocessing
import sys

from autotrade.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        # 輸出被導到紀錄檔時一律用 UTF-8（跟沒打包時一樣）；直接印在視窗上就照視窗自己的編碼
        if stream is not None and not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
