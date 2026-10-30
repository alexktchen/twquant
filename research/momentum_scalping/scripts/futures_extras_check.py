#!/usr/bin/env python3
"""真實逐筆驗證：Extras 的區間突圍要和回測引擎 intraday_bidir_test.run_noise 一筆不差。"""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, '.')
import fubon_live as L, futures_replay as F, intraday_bidir_test as B
L.ms_engine_parts(Path('C:/t/Momentum_Scalping'))
folder = Path(sys.argv[1])
raw = F.read_taifex(folder, 'MTX')
bad = 0
days = sorted(raw)[-12:]
for name in days:
    day = date.fromisoformat(name)
    info = raw[name]
    try:
        hist, last_raw, _ = L.history_days(folder, day)
    except SystemExit:
        continue
    if len(hist) < B.NOISE_LOOKBACK:
        continue
    live = L.LiveCombo(day, info['contract'], last_raw['closes'][info['contract']], hist, lambda m: None)
    for t_us, _, p, s in info['ticks']:
        live.on_tick(t_us, p, s)
    d = B.Day(name, raw[name], last_raw['closes'][info['contract']])
    ref = B.run_noise(d, hist)[0]
    got = [t for t in live.extras.trades if t['sleeve'].startswith('NOISE')]
    a = sorted((t['side'], round(t['entry_price']), round(t['exit_price'])) for t in got)
    b = sorted((t['side'], round(t['entry_price'] if 'entry_price' in t else t['entry']), round(t['exit_price'] if 'exit_price' in t else t['exit'])) for t in ref)
    same = a == b
    bad += not same
    print(name, '區間突圍', len(got), '回測引擎', len(ref), '一致' if same else f'不同 {a} vs {b}', '| 回測確認', sum(t['sleeve'].startswith('BOX') for t in live.extras.trades), '尾盤', sum(t['sleeve'] == 'TAIL_SHORT' for t in live.extras.trades), '| 開倉殘留', [k for k, v in live.extras.pos.items() if v])
print('不一致天數', bad)
