import { apiUrl } from '../api'

export type ArtifactIssue = { artifact_id: string; kind: string; detail: string }

export type ModelArtifact = {
  artifact_id: string
  artifact_state: 'active' | 'archived' | 'rejected'
  model_id: string | null
  train_end: string | null
  features: string[] | null
  horizon_days: number | null
  label: string | null
  artifact_fingerprint: string | null
  config_hash: string | null
  data_hash: string | null
  code_hash: string | null
  oos_rank_ic: number | null
  window_ics: number[] | null
  started_at: null
  ended_at: null
  execution_status: 'unavailable'
  qualification: 'unreviewed'
}

export type BacktestMetrics = Partial<Record<'cagr' | 'sharpe' | 'sortino' | 'mdd' | 'calmar' | 'turnover' | 'win_rate' | 'n_days', number>>
export type BacktestArtifact = {
  artifact_id: string
  artifact_state: 'active' | 'archived' | 'rejected'
  source_name: string
  strategy_name: string | null
  start: string | null
  end: string | null
  source: string | null
  data_fingerprint: string | null
  initial_capital: number | null
  max_participation: number | null
  metrics: BacktestMetrics
}
export type EquityPoint = { date: string; equity: number; cash: number; positions_value: number }
export type FinalHolding = { stock_id: string; shares?: number; last_close?: number; value?: number; weight?: number; avg_cost?: number; ret?: number; max_loss?: number; entry_date?: string }
export type BacktestDetail = BacktestArtifact & { equity: EquityPoint[]; final_holdings: FinalHolding[] | null }
export type ArtifactCatalog<T> = { items: T[]; source: string; issues: ArtifactIssue[] }
export type ArtifactDetail<T> = { item: T; issues: ArtifactIssue[] }

async function getArtifact<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), { headers: { accept: 'application/json' } })
  if (!response.ok) {
    let detail = `${path} → HTTP ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body.detail) detail = String(body.detail)
    } catch {
      // Keep the status result when a proxy returns a non-JSON error body.
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

/** Read safe saved model metadata; this endpoint never starts a training run. */
export const fetchModels = () => getArtifact<ArtifactCatalog<ModelArtifact>>('/api/platform/models')
/** Read a model summary using the server-issued opaque artifact identity. */
export const fetchModel = (artifactId: string) =>
  getArtifact<ArtifactDetail<ModelArtifact>>(`/api/platform/models/${encodeURIComponent(artifactId)}`)
/** Read saved result-bundle summaries without starting a backtest. */
export const fetchBacktests = () => getArtifact<ArtifactCatalog<BacktestArtifact>>('/api/platform/backtests')
/** Read persisted equity and final holdings for one saved backtest result. */
export const fetchBacktestArtifact = (artifactId: string) =>
  getArtifact<ArtifactDetail<BacktestDetail>>(`/api/platform/backtests/${encodeURIComponent(artifactId)}`)
