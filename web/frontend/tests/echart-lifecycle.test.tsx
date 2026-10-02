import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EChartsOption } from 'echarts'
import EChart from '../src/components/EChart'

const chart = vi.hoisted(() => ({ resize: vi.fn(), dispose: vi.fn(), setOption: vi.fn() }))
const init = vi.hoisted(() => vi.fn(() => chart))
vi.mock('echarts/core', () => ({ init, use: vi.fn() }))

let resizeCallback: ResizeObserverCallback
let observe: ReturnType<typeof vi.fn>
let disconnect: ReturnType<typeof vi.fn>
let media: MediaQueryList
let mediaChange: (() => void) | undefined

beforeEach(() => {
  vi.clearAllMocks()
  observe = vi.fn()
  disconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resizeCallback = callback }
    observe = observe
    disconnect = disconnect
  })
  mediaChange = undefined
  media = {
    matches: false,
    addEventListener: vi.fn((_event, callback) => { mediaChange = callback }),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList
  vi.stubGlobal('matchMedia', vi.fn(() => media))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('EChart lifecycle', () => {
  it('resizes with its container and window, then disconnects and ignores queued callbacks', () => {
    const view = render(<EChart option={{}} ariaLabel="账户净值与基准" />)
    expect(observe).toHaveBeenCalledWith(screen.getByRole('img', { name: '账户净值与基准' }))
    act(() => {
      resizeCallback([], {} as ResizeObserver)
      window.dispatchEvent(new Event('resize'))
    })
    expect(chart.resize).toHaveBeenCalledTimes(2)
    view.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
    expect(chart.dispose).toHaveBeenCalledOnce()
    expect(media.removeEventListener).toHaveBeenCalledWith('change', mediaChange)
    act(() => {
      resizeCallback([], {} as ResizeObserver)
      window.dispatchEvent(new Event('resize'))
    })
    expect(chart.resize).toHaveBeenCalledTimes(2)
  })

  it('keeps the window resize fallback when ResizeObserver is unavailable', () => {
    vi.stubGlobal('ResizeObserver', undefined)
    const view = render(<EChart option={{}} />)
    window.dispatchEvent(new Event('resize'))
    expect(chart.resize).toHaveBeenCalledOnce()
    view.unmount()
    window.dispatchEvent(new Event('resize'))
    expect(chart.resize).toHaveBeenCalledOnce()
  })

  it('updates chart options without recreating the chart instance', () => {
    const first: EChartsOption = { series: [{ type: 'pie', data: [{ value: 1, name: '现金' }] }] }
    const next: EChartsOption = { series: [{ type: 'pie', data: [{ value: 2, name: '持仓' }] }] }
    const view = render(<EChart option={first} />)
    expect(chart.setOption).toHaveBeenLastCalledWith(first, { notMerge: true, lazyUpdate: true })
    view.rerender(<EChart option={next} />)
    expect(chart.setOption).toHaveBeenLastCalledWith(next, { notMerge: true, lazyUpdate: true })
    expect(init).toHaveBeenCalledOnce()
    expect(chart.dispose).not.toHaveBeenCalled()
  })

  it('responds to motion preference changes without mutating the caller option', () => {
    const option = Object.freeze({ animation: true })
    render(<EChart option={option} />)
    act(() => {
      Object.defineProperty(media, 'matches', { value: true, configurable: true })
      mediaChange?.()
    })
    expect(chart.setOption).toHaveBeenLastCalledWith({ animation: false }, expect.anything())
    expect(option.animation).toBe(true)
    act(() => {
      Object.defineProperty(media, 'matches', { value: false, configurable: true })
      mediaChange?.()
    })
    expect(chart.setOption).toHaveBeenLastCalledWith(option, expect.anything())
  })

  it('disables animation initially for reduced motion and preserves an explicit false after opt-out', () => {
    Object.defineProperty(media, 'matches', { value: true, configurable: true })
    const view = render(<EChart option={{ animation: true }} />)
    expect(chart.setOption).toHaveBeenLastCalledWith({ animation: false }, expect.anything())
    view.rerender(<EChart option={{ animation: false }} />)
    act(() => {
      Object.defineProperty(media, 'matches', { value: false, configurable: true })
      mediaChange?.()
    })
    expect(chart.setOption).toHaveBeenLastCalledWith({ animation: false }, expect.anything())
  })
})
