import type { ChartMarker } from '../charts'
import type { MarkerGroup } from './eventMarkers'
import { ACC, MUT, UP, WARN } from '../theme'

/**
 * Marker shapes follow PRD-003 §14 so a raw ML pick never looks like a published call.
 * 顏色必須是實際色碼：圖表畫在 canvas 上，`var(--x)` 解析不到會退回黑色。
 */
const STYLE: Record<MarkerGroup['kind'], { shape: ChartMarker['shape']; color: string; label: string }> = {
  ml_candidate: { shape: 'circle', color: ACC, label: 'ML' },
  insight_pick: { shape: 'arrowUp', color: UP, label: '精選' },
  watch: { shape: 'square', color: MUT, label: '觀察' },
  excluded: { shape: 'square', color: WARN, label: '排除' },
}

/** Render one already-mapped group; the count makes same-day events visible on the chart. */
export function markerFor(group: MarkerGroup): ChartMarker {
  const style = STYLE[group.kind]
  const count = group.events.length > 1 ? `×${group.events.length}` : ''
  return {
    time: group.session,
    position: 'belowBar',
    shape: style.shape,
    color: style.color,
    text: `${style.label}${count}${group.mapped ? '(順延)' : ''}`,
  }
}
