import type { BacktestMetrics } from '../api/client'

export function finiteMetric(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function portfolioTotalReturn(metrics: BacktestMetrics | null | undefined): number | null {
  return finiteMetric(
    metrics?.portfolio_total_return ?? metrics?.net_total_return ?? metrics?.net_avg_return,
  )
}

export function portfolioMaxDrawdown(metrics: BacktestMetrics | null | undefined): number | null {
  return finiteMetric(metrics?.portfolio_max_drawdown ?? metrics?.net_max_drawdown)
}

export function portfolioProfitFactor(metrics: BacktestMetrics | null | undefined): number | null {
  return finiteMetric(metrics?.net_profit_factor)
}
