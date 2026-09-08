/**
 * Operations tabs (SPEC-014 §6).
 *
 * The active tab lives in the URL, so the 30s coverage poll and the 3s job poll cannot reset
 * what the operator is looking at. No tab adds a new privileged action: ingestion keeps the
 * existing whitelist and single-concurrency rule, and there is no training or ordering control.
 */
export const operationsTabs = [
  { id: 'data', label: '資料涵蓋' },
  { id: 'gaps', label: '漏抓與補抓' },
  { id: 'jobs', label: '工作中心' },
] as const

export type OperationsTabId = (typeof operationsTabs)[number]['id']
export const DEFAULT_OPERATIONS_TAB: OperationsTabId = 'data'

/** Resolve a requested operations tab, falling back to the default rather than blanking. */
export function parseOperationsTab(value: string | null): OperationsTabId {
  return operationsTabs.some(tab => tab.id === value) ? (value as OperationsTabId) : DEFAULT_OPERATIONS_TAB
}
