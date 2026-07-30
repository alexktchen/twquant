// 內嵌快照（API 不可達時的離線 fallback）；單一 import 點避免各處重複 cast。
import fallbackRaw from './data.json'
import type { Data } from './api'

export const FALLBACK = fallbackRaw as unknown as Data
