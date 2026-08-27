import { describe, expect, it } from 'vitest'

import { pageMeta } from '../src/layout/Topbar'


describe('顶栏路由元数据', () => {
  it.each([
    ['/v2/desk', '指挥舱'],
    ['/v2/intelligence', '市场情报'],
    ['/v2/strategies', '六形态'],
    ['/v2/signals', '信号观察'],
    ['/v2/research', '研究治理'],
    ['/v2/monitor', '监控'],
    ['/v2/review', '复核'],
    ['/v2/system', '系统'],
    ['/v2/compare', '对比'],
  ])('为 %s 返回对应标题', (path, expectedTitle) => {
    expect(pageMeta(path).title).toBe(expectedTitle)
  })

  it('未知路由仍安全回退到选股总览', () => {
    expect(pageMeta('/unknown')).toEqual({
      kicker: 'Overview',
      title: '选股总览',
      sub: '技术形态 + 资金流 + 基本面 三层筛选',
    })
  })
})
