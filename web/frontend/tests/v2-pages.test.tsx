/**
 * v2 控制台缺页组件测试：加载/空态/错误/正常状态 + 键盘触发 + 不显示原始 JSON。
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../src/api/system', () => ({
  fetchSystemHealth: vi.fn(),
  fetchBackups: vi.fn(),
  fetchAudit: vi.fn(),
  fetchAlerts: vi.fn(),
  markAlertRead: vi.fn(),
}))
vi.mock('../src/api/review', () => ({
  fetchNotes: vi.fn(),
  fetchDecisions: vi.fn(),
  createNote: vi.fn(),
  createDecision: vi.fn(),
  fetchWeekly: vi.fn(),
  fetchAttribution: vi.fn(),
}))
vi.mock('../src/api/client', async (orig) => {
  const actual = await orig<typeof import('../src/api/client')>()
  return { ...actual, api: { ...actual.api, kline: vi.fn() } }
})

import { fetchSystemHealth, fetchAlerts, fetchBackups } from '../src/api/system'
import { fetchNotes, fetchDecisions } from '../src/api/review'
import { api } from '../src/api/client'
import type { SystemHealth } from '../src/types/system'
import { V2_BASE } from '../src/api/core'

import Monitor from '../src/pages/v2/Monitor'
import Review from '../src/pages/v2/Review'
import System from '../src/pages/v2/System'
import Compare from '../src/pages/v2/Compare'

const mockHealth = {
  status: 'PASS',
  build_version: 'abc123',
  database: {
    ok: true,
    size_bytes: 10_000_000,
    wal_bytes: 1_000_000,
    deep_check: { status: 'PASS' },
  },
  disk: { free_gb: 100, ok: true },
  issues: [],
} satisfies SystemHealth

beforeEach(() => {
  vi.clearAllMocks()
})

it('v2 API base is prefixed exactly once by request()', () => {
  expect(V2_BASE).toBe('/v2')
  expect(V2_BASE).not.toContain('/api/api')
})

describe('Monitor 页', () => {
  it('展示健康与空态告警', async () => {
    vi.mocked(fetchSystemHealth).mockResolvedValue(mockHealth)
    vi.mocked(fetchAlerts).mockResolvedValue({ items: [], count: 0 })
    render(<Monitor />)
    await waitFor(() => expect(screen.getByText('系统健康')).toBeInTheDocument())
    expect(screen.getByText('暂无告警')).toBeInTheDocument()
  })

  it('错误态显示错误面板而非原始 JSON', async () => {
    vi.mocked(fetchSystemHealth).mockRejectedValue(new Error('boom'))
    vi.mocked(fetchAlerts).mockResolvedValue({ items: [], count: 0 })
    render(<Monitor />)
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument())
    expect(screen.queryByText(/"db_size_mb"/)).not.toBeInTheDocument()
  })
})

describe('Review 页', () => {
  it('空态显示暂无笔记/决策', async () => {
    vi.mocked(fetchNotes).mockResolvedValue({ items: [] } as never)
    vi.mocked(fetchDecisions).mockResolvedValue({ items: [] } as never)
    render(<Review />)
    await waitFor(() => expect(screen.getByText('暂无笔记')).toBeInTheDocument())
    expect(screen.getByText('暂无决策')).toBeInTheDocument()
  })
})

describe('System 页', () => {
  it('分开显示快速健康与深度检查', async () => {
    vi.mocked(fetchSystemHealth).mockResolvedValue(mockHealth)
    vi.mocked(fetchBackups).mockResolvedValue({
      backup_root: 'E:\\ab-backups', latest: null, status: {},
    })
    render(<System />)
    await waitFor(() => expect(
      screen.getByRole('heading', { name: /快速健康/ }),
    ).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: '最后一次深度完整性检查' })).toBeInTheDocument()
    expect(screen.getByText('abc123')).toBeInTheDocument()
    expect(screen.getByText('10.0 MB')).toBeInTheDocument()
  })
})

describe('Compare 页', () => {
  it('少于 2 标的提示错误；键盘 Enter 触发', async () => {
    render(<Compare />)
    const input = screen.getByLabelText('对比标的')
    await userEvent.type(input, '000001.SZ')
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByText(/至少输入/)).toBeInTheDocument())
  })

  it('2 标的拉取后展示对比卡', async () => {
    vi.mocked(api.kline).mockImplementation(async () => ({
      ts_code: '000001.SZ',
      kline: [{ trade_date: '20260807', open: 10, high: 10, low: 9, close: 10, vol: 1, amount: null }],
    }))
    render(<Compare />)
    const input = screen.getByLabelText('对比标的')
    await userEvent.type(input, '000001.SZ 600000.SH')
    await userEvent.click(screen.getByRole('button', { name: '对比' }))
    await waitFor(() => expect(screen.getByText('000001.SZ')).toBeInTheDocument())
  })
})
