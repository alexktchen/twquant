// 鏡像自 configs/stop_hit_reference.json（D-191 §三 選項 C）。不要手改：tests/test_stop_hit_reference.py 會比對兩份相同；重算只能另立登記。
export const STOP_HIT_TABLE = {
  "registered": "DECISIONS.md D-191 §三-1（2026-10-18，使用者選 C）",
  "dev_start": "2017-09-26",
  "dev_end": "2026-09-08",
  "horizon_days": 20,
  "stop": -0.15,
  "surge": 0.2,
  "vol_definition": "近 20 個交易日簡單日報酬的樣本標準差（ddof=1），收盤不還原除權息；同 factors/price.vol_20d",
  "bounds": [
    0.007405,
    0.010104,
    0.012631,
    0.015261,
    0.018182,
    0.021728,
    0.026174,
    0.031577,
    0.040083
  ],
  "n_obs": 3491606,
  "n_stocks": 2061,
  "n_dropped_events": 264887,
  "groups": [
    {
      "group": 0,
      "vol_lo": null,
      "vol_hi": 0.007405,
      "vol_median": 0.005758,
      "n": 349161,
      "down15": 0.0137,
      "up20": 0.0172,
      "median_ret": 0.0027
    },
    {
      "group": 1,
      "vol_lo": 0.007405,
      "vol_hi": 0.010104,
      "vol_median": 0.008799,
      "n": 349160,
      "down15": 0.0255,
      "up20": 0.0317,
      "median_ret": 0.0017
    },
    {
      "group": 2,
      "vol_lo": 0.010104,
      "vol_hi": 0.012631,
      "vol_median": 0.01137,
      "n": 349161,
      "down15": 0.0382,
      "up20": 0.0457,
      "median_ret": 0.0
    },
    {
      "group": 3,
      "vol_lo": 0.012631,
      "vol_hi": 0.015261,
      "vol_median": 0.013928,
      "n": 349160,
      "down15": 0.0488,
      "up20": 0.0567,
      "median_ret": 0.0
    },
    {
      "group": 4,
      "vol_lo": 0.015261,
      "vol_hi": 0.018182,
      "vol_median": 0.016681,
      "n": 349161,
      "down15": 0.0604,
      "up20": 0.0669,
      "median_ret": -0.0013
    },
    {
      "group": 5,
      "vol_lo": 0.018182,
      "vol_hi": 0.021728,
      "vol_median": 0.01984,
      "n": 349160,
      "down15": 0.0754,
      "up20": 0.0807,
      "median_ret": -0.0023
    },
    {
      "group": 6,
      "vol_lo": 0.021728,
      "vol_hi": 0.026174,
      "vol_median": 0.023878,
      "n": 349161,
      "down15": 0.0918,
      "up20": 0.0991,
      "median_ret": -0.0033
    },
    {
      "group": 7,
      "vol_lo": 0.026174,
      "vol_hi": 0.031577,
      "vol_median": 0.028674,
      "n": 349160,
      "down15": 0.1109,
      "up20": 0.1182,
      "median_ret": -0.0043
    },
    {
      "group": 8,
      "vol_lo": 0.031577,
      "vol_hi": 0.040083,
      "vol_median": 0.035202,
      "n": 349161,
      "down15": 0.1461,
      "up20": 0.1582,
      "median_ret": -0.003
    },
    {
      "group": 9,
      "vol_lo": 0.040083,
      "vol_hi": null,
      "vol_median": 0.04778,
      "n": 349161,
      "down15": 0.224,
      "up20": 0.2446,
      "median_ret": 0.0025
    }
  ],
  "picks_by_group": {
    "CORE": [
      0,
      0,
      0,
      6,
      13,
      19,
      24,
      41,
      153,
      794
    ],
    "QUIET": [
      0,
      0,
      0,
      0,
      3,
      13,
      71,
      268,
      695,
      0
    ]
  }
} as const
