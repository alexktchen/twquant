// 首頁版面的狀態（使用者 2026-10-17：「依照使用者帳號儲存擺置位置」）：打開時先照這台瀏覽器存的排，
// 帳號那份（/api/layout）讀回來再換上；之後每次移動或收起都存回帳號，依序送出（不會後送的蓋掉先送的）。
// 沒登入或存不了時照樣能用，只是只記在這台瀏覽器。
import { useEffect, useRef, useState } from 'react'
import { fetchLayout, resetLayout, saveLayout } from './api'
import { LoginRequired } from './auth'
import { LAYOUT_KEY, browserLayout, defaultLayout, normalizeLayout, toggleCollapsed, withOrder, type CardId, type Layout } from './layout'
import { savePref } from './prefs'

export function useHomeLayout(): {
  layout: Layout
  toggle: (id: CardId) => void
  setOrder: (order: CardId[]) => void
  reset: () => void
  /** 存到帳號失敗的原因（空字串＝沒事） */
  error: string
} {
  const [layout, setLayout] = useState<Layout>(browserLayout)
  const [error, setError] = useState('')
  const chain = useRef<Promise<void>>(Promise.resolve())
  // 帳號那份讀回來之前使用者就動了 → 以使用者這次的為準，不被讀回來的蓋掉
  const touched = useRef(false)
  const current = useRef(layout)

  useEffect(() => {
    let gone = false
    fetchLayout()
      .then((r) => {
        if (gone || touched.current || r.layout === null) return
        const l = normalizeLayout(r.layout)
        current.current = l
        setLayout(l)
        savePref(LAYOUT_KEY, JSON.stringify(l))
      })
      // 沒登入（訪客）或暫時讀不到：照這台瀏覽器的排
      .catch(() => undefined)
    return () => {
      gone = true
    }
  }, [])

  const send = (job: () => Promise<unknown>) => {
    chain.current = chain.current
      .then(job)
      .then(() => setError(''))
      .catch((e: Error) => setError(e instanceof LoginRequired ? '' : `版面沒有存到帳號（${e.message}），這台裝置仍會記得。`))
  }

  const commit = (next: Layout) => {
    touched.current = true
    current.current = next
    setLayout(next)
    savePref(LAYOUT_KEY, JSON.stringify(next))
    send(() => saveLayout(next))
  }

  return {
    layout,
    error,
    toggle: (id) => commit(toggleCollapsed(current.current, id)),
    setOrder: (order) => commit(withOrder(current.current, order)),
    reset: () => {
      const d = defaultLayout()
      touched.current = true
      current.current = d
      setLayout(d)
      savePref(LAYOUT_KEY, JSON.stringify(d))
      send(() => resetLayout())
    },
  }
}
