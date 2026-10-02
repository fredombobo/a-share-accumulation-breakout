import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { forwardApi, type ForwardCapture, type ForwardOutcome, type ForwardStatus } from '../src/api/forward'
import ForwardObservations from '../src/pages/ForwardObservations'

vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }))
const empty: ForwardStatus = {
  enabled: false, protocol: null, counts: { captures: 0, primary: 0, secondary: 0, candidates: 0, outcomes: 0 },
  last_attempt: null, last_error: null, note: '收盘价变化，不是成交收益。',
}
const enabled: ForwardStatus = { ...empty, enabled: true, protocol: { version: 'forward-test-v3', enabled_at: '2026-09-13T23:00:00+08:00', horizons: [1, 5, 10, 20], note: empty.note } }
const capture: ForwardCapture = { run_id: 'scan-new', as_of: '20260911', captured_at: '2026-09-13T23:01:00+08:00', entry_hash: 'entry-frozen', config_hash: 'config-full', role: 'PRIMARY', counts: { A: 0, B: 1, DATA_INCOMPLETE: 1, total: 2 } }
const pending: ForwardOutcome = { run_id: capture.run_id, ts_code: '600176.SH', role: 'PRIMARY', pool: 'B', group: 'B', as_of: capture.as_of, target_date: '20260918', horizon: 5, status: 'WAITING_HORIZON', raw_return: null, adjusted_return: null, revision: 1, computed_at: capture.captured_at }

beforeEach(() => {
  vi.spyOn(forwardApi, 'status').mockResolvedValue(empty)
  vi.spyOn(forwardApi, 'history').mockResolvedValue({ items: [], total: 0 })
  vi.spyOn(forwardApi, 'enable').mockResolvedValue(enabled)
  vi.spyOn(forwardApi, 'refresh').mockResolvedValue({ status: 'COMPLETE', appended: 0 })
  vi.spyOn(forwardApi, 'capture').mockResolvedValue({ status: 'REJECTED', captured: false, message: '已超过记录期限' })
  vi.spyOn(forwardApi, 'results').mockResolvedValue({ items: [], total: 0, note: empty.note })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('前瞻观察的真实状态', () => {
  it('切换到另一页历史名单时重置结果页码', async () => {
    vi.mocked(forwardApi.status).mockResolvedValue(enabled)
    vi.mocked(forwardApi.history).mockImplementation(async offset => ({ items: [{ ...capture, run_id: offset ? 'scan-older' : capture.run_id }], total: 51 }))
    vi.mocked(forwardApi.results).mockImplementation(async (runId, horizon, offset) => ({
      items: runId === 'scan-older' && offset ? [] : [{ ...pending, run_id: runId, ts_code: runId === 'scan-older' ? '600000.SH' : pending.ts_code, horizon }],
      total: runId === 'scan-older' ? 1 : 150, note: empty.note,
    }))
    render(<ForwardObservations />)
    fireEvent.click(await screen.findByRole('button', { name: '下一页' }))
    await waitFor(() => expect(forwardApi.results).toHaveBeenLastCalledWith('scan-new', 5, 100, false, expect.any(Object)))
    fireEvent.click(screen.getByRole('button', { name: '较早记录' }))
    expect(await screen.findByRole('rowheader', { name: '600000.SH' })).toBeVisible()
    expect(forwardApi.results).toHaveBeenLastCalledWith('scan-older', 5, 0, false, expect.any(Object))
  })

  it('结果请求失败后重新读取会实际重试结果接口', async () => {
    vi.mocked(forwardApi.status).mockResolvedValue(enabled)
    vi.mocked(forwardApi.history).mockResolvedValue({ items: [capture], total: 1 })
    vi.mocked(forwardApi.results).mockRejectedValueOnce(new Error('观察读取失败')).mockResolvedValue({ items: [pending], total: 1, note: empty.note })
    render(<ForwardObservations />)
    expect(await screen.findByRole('alert')).toHaveTextContent('观察读取失败')
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    expect(await screen.findByRole('rowheader', { name: '600176.SH' })).toBeVisible()
    expect(forwardApi.results).toHaveBeenCalledTimes(2)
  })
  it('数据后补只显示为独立修订，不替换原始观察列', async () => {
    vi.mocked(forwardApi.status).mockResolvedValue(enabled)
    vi.mocked(forwardApi.history).mockResolvedValue({ items: [capture], total: 1 })
    vi.mocked(forwardApi.results).mockResolvedValue({ items: [{ ...pending, status: 'RAW_ONLY', raw_return: 0.1,
      restated: { basis: 'LATEST_SOURCE_AT_REFRESH', differs_from_capture: true, raw_return: -0.45, adjusted_return: null, status: 'RAW_ONLY' } }], total: 1, note: empty.note })
    render(<ForwardObservations />)
    expect(await screen.findByRole('cell', { name: '+10.00%' })).toBeVisible()
    expect(screen.queryByRole('cell', { name: '-45.00%' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('基准数据有后补或修订'))
    expect(screen.getByText(/按当前修订数据计算：原价 -45.00%/)).toBeVisible()
  })
  it('打开页面只读取，用户启用后才开始记录', async () => {
    render(<ForwardObservations />)
    const start = await screen.findByRole('button', { name: '启用前瞻记录' })
    expect(forwardApi.enable).not.toHaveBeenCalled()
    expect(forwardApi.refresh).not.toHaveBeenCalled()
    expect(forwardApi.capture).not.toHaveBeenCalled()
    vi.mocked(forwardApi.status).mockResolvedValue(enabled)
    fireEvent.click(start)
    expect(await screen.findByText('等待下一次成功扫描')).toBeVisible()
    expect(forwardApi.enable).toHaveBeenCalledOnce()
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
  })

  it('未到期和缺数据保持空值，零变化可以显示为真实零值', async () => {
    vi.mocked(forwardApi.status).mockResolvedValue(enabled)
    vi.mocked(forwardApi.history).mockResolvedValue({ items: [capture], total: 1 })
    vi.mocked(forwardApi.results).mockResolvedValue({ items: [pending, { ...pending, ts_code: '600000.SH', group: 'DATA_INCOMPLETE', status: 'RAW_ONLY', raw_return: 0 }], total: 2, note: empty.note })
    render(<ForwardObservations />)
    const table = await screen.findByRole('table')
    const pendingRow = within(table).getByRole('rowheader', { name: '600176.SH' }).closest('tr')!
    expect(within(pendingRow).getAllByText('—')).toHaveLength(2)
    expect(within(pendingRow).getByText('尚未到期')).toBeVisible()
    expect(within(table).getAllByText('0.00%')).toHaveLength(1)
    expect(within(table).getByText('数据不足')).toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: '20 日' }))
    await waitFor(() => expect(forwardApi.results).toHaveBeenLastCalledWith('scan-new', 20, 0, false, expect.any(Object)))
  })

  it('零候选也保留记录，不伪造个股观察', async () => {
    vi.mocked(forwardApi.status).mockResolvedValue(enabled)
    vi.mocked(forwardApi.history).mockResolvedValue({ items: [{ ...capture, counts: { A: 0, B: 0, DATA_INCOMPLETE: 0, total: 0 } }], total: 1 })
    render(<ForwardObservations />)
    expect(await screen.findByText('这次扫描为零候选，记录已保留')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('主样本记录失败不会被重复观察替代，也不把拒绝写成成功', async () => {
    vi.mocked(forwardApi.status).mockResolvedValue({ ...enabled, missing_primary_runs: ['scan-first'], uncaptured_runs: ['scan-first'] })
    render(<ForwardObservations />)
    expect(await screen.findByText(/预定主样本尚未成功记录/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重试首个待记录扫描' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('已超过记录期限')
    expect(screen.queryByText('已处理记录请求，请核对主样本状态。')).not.toBeInTheDocument()
    expect(forwardApi.capture).toHaveBeenCalledWith('scan-first')
  })
})
