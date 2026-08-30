import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, type BacktestTask, type ScanStatus, type SyncStatus } from '../src/api/client'
import GlobalRunProgress from '../src/components/GlobalRunProgress'

const idleScan: ScanStatus = {
  id: '', status: 'idle', stage: '无任务', progress: 0, cancel_requested: false,
}

const idleSync: SyncStatus = {
  status: 'idle', message: '', started_at: null, finished_at: null,
  latest_daily: '20260828', latest_moneyflow: '20260828', failed_dates: [],
}

function renderProgress() {
  return render(<MemoryRouter><GlobalRunProgress /></MemoryRouter>)
}

afterEach(() => vi.restoreAllMocks())

describe('全局运行进度', () => {
  it('在任意页面显示扫描真实百分比、阶段和耗时', async () => {
    const now = new Date()
    vi.spyOn(api, 'scanStatus').mockResolvedValue({
      id: 'scan-visible', status: 'running', stage: '技术形态检测', progress: 37,
      cancel_requested: false,
      started_at: new Date(now.getTime() - 75_000).toISOString(),
      updated_at: new Date(now.getTime() - 2_000).toISOString(),
    })
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null })
    vi.spyOn(api, 'syncStatus').mockResolvedValue(idleSync)

    renderProgress()

    const progress = await screen.findByRole('progressbar', { name: '全市场扫描进度' })
    expect(progress).toHaveAttribute('aria-valuenow', '37')
    expect(screen.getByText('技术形态检测')).toBeVisible()
    expect(screen.getByText(/已运行 1分/)).toBeVisible()
    expect(screen.getByRole('button', { name: '查看任务' })).toBeVisible()
  })

  it('长时间无真实推进时给出谨慎的疑似卡住提示', async () => {
    const now = Date.now()
    const task: BacktestTask = {
      task_id: 'probt-stalled', research_run_id: 'probt-stalled', research_mode: 'professional_grid',
      status: 'running', phase: 'WF', progress: 64, message: '滚动窗口复验',
      request: {} as BacktestTask['request'], result: null,
      created_at: new Date(now - 600_000).toISOString(),
      started_at: new Date(now - 600_000).toISOString(),
      updated_at: new Date(now - 190_000).toISOString(),
      heartbeat_at: new Date(now - 190_000).toISOString(),
    }
    vi.spyOn(api, 'scanStatus').mockResolvedValue(idleScan)
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task })
    vi.spyOn(api, 'syncStatus').mockResolvedValue(idleSync)

    renderProgress()

    expect(await screen.findByRole('progressbar', { name: '专业回测进度' })).toHaveAttribute('aria-valuenow', '64')
    expect(screen.getByText(/无进度变化，可能仍在重计算/)).toBeVisible()
  })

  it('行情供应商没有分项百分比时使用不定进度并明确披露', async () => {
    vi.spyOn(api, 'scanStatus').mockResolvedValue(idleScan)
    vi.spyOn(api, 'backtestLatest').mockResolvedValue({ task: null })
    vi.spyOn(api, 'syncStatus').mockResolvedValue({
      ...idleSync,
      status: 'running',
      message: '开始同步行情',
      started_at: new Date(Date.now() - 20_000).toISOString(),
    })

    renderProgress()

    const progress = await screen.findByRole('progressbar', { name: '行情同步进度' })
    expect(progress).not.toHaveAttribute('aria-valuenow')
    expect(progress).toHaveAttribute('aria-valuetext', '进行中，数据源未提供百分比')
    expect(screen.getByText(/供应商未提供分项百分比/)).toBeVisible()
  })
})
