import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import Topbar from '../src/layout/Topbar'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, useLocation: () => ({ pathname: '/' }) }))
vi.mock('../src/theme/ThemeContext', () => ({ useTheme: () => ({ theme: 'light', toggle: vi.fn() }) }))
vi.mock('../src/components/GlobalRunProgress', () => ({ RUN_TASK_EVENT: 'run-task' }))

const stocks = [
  { ts_code: '000001.SZ', name: '平安银行', industry: '银行' },
  { ts_code: '600036.SH', name: '招商银行', industry: '银行' },
  { ts_code: '002142.SZ', name: '宁波银行', industry: '银行' },
]

function submit(query: string) {
  fireEvent.change(screen.getByRole('textbox', { name: '股票名称或代码' }), { target: { value: query } })
  fireEvent.submit(screen.getByRole('search', { name: '查询个股' }))
}

beforeEach(() => {
  navigate.mockReset()
  vi.spyOn(api, 'syncStatus').mockResolvedValue({ status: 'idle', message: '', started_at: null,
    finished_at: null, latest_daily: '20260911', latest_moneyflow: '20260911', failed_dates: [] })
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Topbar stock lookup', () => {
  it.each([
    ['600000', '600000.SH'],
    ['000001', '000001.SZ'],
    ['920001', '920001.BJ'],
  ])('opens six-digit code %s without a search request', async (query, code) => {
    const search = vi.spyOn(api, 'searchStocks').mockResolvedValue(stocks)
    render(<Topbar />)
    submit(query)
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(`/stock/${code}`))
    expect(search).not.toHaveBeenCalled()
  })

  it('opens an exact stock name and offers selectable results for a partial name', async () => {
    const search = vi.spyOn(api, 'searchStocks').mockImplementation((query) => Promise.resolve(query === '平安银行' ? [stocks[0]] : stocks))
    render(<Topbar />)
    submit('平安银行')
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/stock/000001.SZ'))
    expect(search).toHaveBeenCalledWith('平安银行')
    navigate.mockClear()
    submit('银行')
    const choice = await screen.findByRole('button', { name: /招商银行.*600036.SH/ })
    expect(screen.getByRole('button', { name: /平安银行.*000001.SZ/ })).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
    fireEvent.click(choice)
    expect(navigate).toHaveBeenCalledWith('/stock/600036.SH')
    expect(search).toHaveBeenCalledWith('银行')
  })

  it('does not request stock search for a blank query', async () => {
    const search = vi.spyOn(api, 'searchStocks').mockResolvedValue(stocks)
    render(<Topbar />)
    submit('   ')
    expect(await screen.findByRole('alert')).toHaveTextContent('请输入股票名称或六位代码')
    expect(search).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('reports a search request failure and keeps direct code lookup usable', async () => {
    const search = vi.spyOn(api, 'searchStocks').mockRejectedValue(new Error('unavailable'))
    render(<Topbar />)
    submit('平安银行')
    expect(await screen.findByRole('alert')).toHaveTextContent(/暂不可用.*六位代码/)
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '查询' })).toBeEnabled()
    submit('000001')
    expect(navigate).toHaveBeenCalledWith('/stock/000001.SZ')
    expect(search).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not navigate from an old search response after the input changes', async () => {
    let resolveSearch!: (value: typeof stocks) => void
    const pending = new Promise<typeof stocks>((resolve) => { resolveSearch = resolve })
    vi.spyOn(api, 'searchStocks').mockReturnValue(pending)
    render(<Topbar />)
    submit('平安银行')
    expect(screen.getByRole('button', { name: '查询中' })).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: '股票名称或代码' }), { target: { value: '招商银行' } })
    await act(async () => { resolveSearch([stocks[0]]); await pending })
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: '股票名称或代码' })).toHaveValue('招商银行')
    expect(screen.getByRole('button', { name: '查询' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /平安银行.*000001.SZ/ })).not.toBeInTheDocument()
  })
})
