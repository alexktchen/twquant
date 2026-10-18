// 個股頁「同波動股票 20 日內碰 −15%／+20%」一行（D-191 §三 選項 C）。歷史基礎率的查表，不是預測。
import { useEffect, useState } from 'react'
import { fetchStockStopHit } from './api'
import { stopHitLine, stopHitTitle, type StopHit } from './stophit'

export function StockStopHit({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<{ sid: string; hit: StopHit | null } | null>(null)

  useEffect(() => {
    let stale = false
    fetchStockStopHit(sid)
      .then((r) => !stale && setData(r))
      .catch(() => !stale && setData({ sid, hit: null })) // 讀不到不擋個股頁其他部分
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const hit = data?.sid === sid ? data.hit : null
  const line = stopHitLine(hit)
  if (!line || !hit) return null
  return (
    <div className="cq-ref cq-stophit-block">
      <h3>
        停損基礎率<small>同波動股票的歷史比例，不是對這檔的預測</small>
      </h3>
      <p title={stopHitTitle(hit)}>
        {line}
        <small>（日波動度 {(hit.vol * 100).toFixed(2)}%，全市場第 {hit.group + 1} 組；2017-09～2026-09 共 {hit.n.toLocaleString('zh-TW')} 筆）</small>
      </p>
      <p className="cq-ref-note">波動度＝近 20 個交易日日報酬的標準差。高波動是雙向的：容易碰到 −15% 停損，也差不多容易漲過 +20%。只供參考，名單不因此刪減。</p>
    </div>
  )
}
