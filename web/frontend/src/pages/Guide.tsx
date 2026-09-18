import { useEffect, useState } from 'react'

import { api, type ClassificationCatalogResp } from '../api/client'

const contents = [
  ['quick-start', '每天怎么用'],
  ['selection-logic', '系统怎么选股'],
  ['pools', 'A 池和 B 池'],
  ['classifications', '板块分类标准'],
  ['backtest', '研究回测'],
  ['profile-loop', '今日扫描参数怎么选'],
  ['forward-records', '前瞻观察与复盘'],
  ['results', '如何阅读结果'],
  ['progress', '进度与异常'],
  ['boundaries', '系统边界'],
] as const

export default function Guide() {
  const [catalog, setCatalog] = useState<ClassificationCatalogResp | null>(null)

  useEffect(() => {
    let active = true
    api.classifications().then((data) => active && setCatalog(data)).catch(() => undefined)
    return () => { active = false }
  }, [])

  return (
    <div className="manual-shell fade-up">
      <section className="manual-intro">
        <div>
          <span className="guide-eyebrow">AB-Screener 操作手册</span>
          <h1>从更新行情到读懂回测</h1>
          <p>本页解释系统做什么、每天点哪里、筛选依据是什么，以及哪些结论不能直接用于交易。</p>
        </div>
        <div className="manual-quickline">
          <b>最短使用路径</b>
          <span>更新行情</span><i />
          <span>运行扫描</span><i />
          <span>查看 A 池证据</span>
        </div>
      </section>

      <div className="manual-layout">
        <nav className="manual-toc" aria-label="说明书目录">
          <strong>目录</strong>
          {contents.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
        </nav>

        <main className="manual-content">
          <section id="quick-start" className="manual-section">
            <h2>每天怎么用</h2>
            <p>按“每日选股 → 研究回测 → 前瞻观察”使用。晚上更新行情并扫描，次日开盘前核对名单，每周在观察页复盘后续表现。系统保存研究证据，不执行真实或纸面交易。</p>
            <div className="manual-flow">
              <article><b>更新行情</b><p>顶部点“更新”。确认数据日是最新已完成交易日，过期数据不能当作今日结果。</p></article>
              <article><b>运行扫描</b><p>首页点“扫描”。顶部全局进度会显示真实阶段、百分比、耗时和最近推进。</p></article>
              <article><b>核对名单状态</b><p>A 池通过严格形态和当日门禁。防守环境下的严格形态保留在 B 池，数据不足也单独标注。零候选属于有效结果。</p></article>
              <article><b>核对单股证据</b><p>打开个股详情，检查箱体、突破量、资金流、风险提示和 AI 本地证据。</p></article>
            </div>
          </section>

          <section id="selection-logic" className="manual-section">
            <h2>系统怎么选股</h2>
            <p>核心经济假设是“较长时间横盘吸筹后出现可验证的放量突破”。系统按以下顺序失败关闭：</p>
            <dl className="logic-list">
              <div><dt>数据时点</dt><dd>只使用决策时点已经可用的数据。行情过期、缺字段或交易日不完整时不宣称有效结果。</dd></div>
              <div><dt>技术形态</dt><dd>检查箱体持续时间、振幅、支撑压力触及、中部占用、漂移、突破幅度和双重量能。</dd></div>
              <div><dt>资金与基本面</dt><dd>核对主力资金方向、成交额、估值和可用财务指标。缺失项会披露，不会静默补值。</dd></div>
              <div><dt>市场环境</dt><dd>防守环境禁止新开仓，A 池可以为空。空池是风险结果，不代表程序失效。</dd></div>
              <div><dt>候选分层</dt><dd>严格满足门禁的进入 A 池；放宽或主题观察进入 B 池。</dd></div>
            </dl>
          </section>

          <section id="pools" className="manual-section">
            <h2>A 池和 B 池怎么理解</h2>
            <div className="manual-compare">
              <article><b>A 池，严格研究候选</b><p>通过严格筛选和当日市场环境检查。它仍只是学习与研究结果，不是荐股或买入指令。</p></article>
              <article><b>B 池，观察名单</b><p>包含放宽、主题补充、数据不足，以及受市场环境或展示额度限制的严格形态。详情保留原层级和原因。</p></article>
            </div>
          </section>

          <section id="classifications" className="manual-section">
            <h2>板块分类标准</h2>
            <p>首页资金图和研究回测使用同一套分类定义。切换分类只改变如何分组，不改变原始资金流或股票行情。</p>
            <div className="classification-manual" aria-label="当前分类能力">
              {(catalog?.items || []).map((item) => (
                <article key={item.key}>
                  <div><b>{item.title}</b><span>{item.group_count} 个{item.group_label}</span></div>
                  <p>{item.description}</p>
                  <small>当前覆盖 {item.coverage_pct.toFixed(1)}%。示例：{item.examples.slice(0, 4).join('、') || '暂无'}</small>
                </article>
              ))}
              {!catalog && <div className="loading">正在读取本地分类能力...</div>}
            </div>
            <div className="manual-warning">
              <b>分类时点限制</b>
              <p>{catalog?.limitations || '分类来自当前 stock_basic 快照，不等同历史成员 PIT。'}</p>
              <p>申万、中信和概念板块只有在补齐历史成员、available_at 和版本数据后才能开放正式回测。</p>
            </div>
          </section>

          <section id="backtest" className="manual-section">
            <h2>研究回测怎么操作</h2>
            <ol className="manual-steps">
              <li><b>冻结股票池</b><span>选择细分行业、上市板块或地域，再多选分组。也可直接填写股票代码，代码优先。</span></li>
              <li><b>设置参数空间</b><span>默认搜索横盘最长 60 至 200 日、突破量比、止损、止盈、最长持有和二次出货观察窗。止损/止盈输入直接按百分比显示。</span></li>
              <li><b>检查参数空间</b><span>检查结束会弹出成功或失败结果，直接显示有效组合、冻结股票数、动态预热和研究窗口。弹窗不会启动任务；任何输入变化后都要重新检查。</span></li>
              <li><b>启动并等待</b><span>硬上限为 5,120 组；超过 512 组会弹出长耗时确认而不是直接拒绝。任务依次完成数据冻结、IS/OOS、WF、基准、成本压力和结论，切页不会中断。</span></li>
            </ol>
            <p className="manual-note">收盘信号最早在下一交易日开盘模拟成交，不存在同一收盘价无摩擦成交路径。</p>
          </section>

          <section id="profile-loop" className="manual-section">
            <h2>今日扫描参数可以怎么选</h2>
            <ol className="manual-steps">
              <li><b>调整筛选条件</b><span>每日页点“编辑筛选条件”，可填写横盘、突破和量比等入选条件，无需先跑回测。保存后只影响下一次扫描，并标记为“未回测验证”。</span></li>
              <li><b>复制入场条件</b><span>研究结果支持的基础机制可复制九项入场条件。退出参数由服务端保留，新配置标记待验证；原报告的收益不能归属于复制后的配置。</span></li>
              <li><b>也可使用回测档案</b><span>回测结论达到探索候选门槛且代码、数据身份仍有效时，结果页才会出现人工启用按钮。系统绝不自动上线最佳曲线。</span></li>
              <li><b>或恢复系统默认</b><span>默认、手工、条件复制、通过门槛的回测档案均清楚标记。切换不会删除历史档案和扫描审计。</span></li>
              <li><b>运行今日扫描</b><span>首页会显示当前参数版本。扫描启动时冻结该快照和哈希，A 池技术入场检测使用同一组横盘、突破和量能参数。</span></li>
              <li><b>集中设置退出规则</b><span>止盈、止损、最长持有和高级退出条件统一放在研究回测页。它们不决定是否入选；每日页修改筛选条件时会保留原有退出方案。</span></li>
              <li><b>分清两个时间参数</b><span>最长持有决定到期退出；二次出货观察窗只决定量能累计检查范围。排行榜按精确权益路径折叠等效参数，不把重复结果当成额外证据。</span></li>
            </ol>
            <div className="manual-warning">
              <b>为什么回测结果与 A 池不一定逐只相同</b>
              <p>闭环统一的是横盘吸筹突破的技术入场参数。今日扫描还承担数据新鲜度、市场环境、资金质量、基本面和评分门禁；这些门禁是为了让当日候选更可用，不能为了复刻回测而绕过。</p>
            </div>
          </section>

          <section id="forward-records" className="manual-section">
            <h2>前瞻观察与复盘</h2>
            <ol className="manual-steps">
              <li><b>先启用，再扫描</b><span>前瞻页保存真实启用时间。只有之后开始且在下一交易日 09:15 前发布的新扫描可以记录，旧扫描不补记为前瞻。</span></li>
              <li><b>冻结完整名单</b><span>保存完整 A、B、数据不足分组及空结果。展示数量只影响首页呈现，不改变完整资格。相同条件的首次合格发布预定为主样本，重复扫描保留为次样本。</span></li>
              <li><b>按交易日跟踪</b><span>观察扫描日收盘至未来 1、5、10、20 个交易日收盘的价格变化。未到期、缺行情、停牌或缺复权因子都会明确标注，未知值不填零。</span></li>
              <li><b>每周核对证据</b><span>点击“更新到期观察”，按原始分组比较；查看修订版本和下载证据。价格观察没有模拟成交、费用、仓位与滑点，不能当作账户收益。</span></li>
            </ol>
            <p className="manual-note">本轮四组原参数重算是对已观察历史的纠错，不是新的独立验证。前瞻样本需要实际等待，不会事后挑选更好的扫描替换主样本。</p>
          </section>

          <section id="results" className="manual-section">
            <h2>如何阅读回测结果</h2>
            <p>先看数据范围和样本数。自动窗口仅按数据时点完整性及预热需要收缩，不根据收益挑选日期。当前分类样本按交易所和行业固定种子分层，不再取代码排序前 N 只；指定代码超过上限会明确拒绝，不会偷偷裁掉。</p>
            <dl className="term-grid">
              <div><dt>IS</dt><dd>样本内，只用于选择参数。</dd></div>
              <div><dt>OOS</dt><dd>样本外，用未参与选参的数据验证。</dd></div>
              <div><dt>WF</dt><dd>滚动窗口复验，检查不同时间段稳定性。</dd></div>
              <div><dt>基准</dt><dd>随机和均线策略对照，避免只看自己的曲线。</dd></div>
              <div><dt>成本压力</dt><dd>提高滑点和费用后复算，检验收益是否脆弱。</dd></div>
              <div><dt>最大回撤</dt><dd>历史模拟中从高点到低点的最大跌幅。</dd></div>
            </dl>
            <div className="manual-warning">
              <b>先看结论，再看五类结果图</b>
              <p>净收益和 Profit Factor 对照比较 IS、OOS、2 倍成本与双基线；风险收益分布显示每组参数的 OOS 收益与最大回撤；前十图保持 IS 原排名；WF 图逐窗比较训练与测试 PF。</p>
              <p>图表只画接口返回的真实指标。没有逐日净值序列时不会伪造净值曲线，缺字段会明确显示暂无可绘制数据。</p>
              <p>新版“账户复盘”显示入选参数的真实净资产曲线、月度含浮盈亏收益、个股/当前行业/退出原因的已实现损益，以及逐笔现金与费用。不要把已实现归因误读成包含未平仓浮盈亏的行业总收益。</p>
              <p>低于 30 笔会如实显示实际数量，并保留样本不足结论。旧报告未保存过滤前数量，其“0”不能直接证明没有信号；需要新建复验，而不是改写旧报告。</p>
            </div>
            <p>回测页的结论是探索证据。只有门槛通过后，用户才能把它作为“回测来源”的每日 A 池技术参数；用户仍可独立采用手工参数，但手工参数不会冒充已验证结论。</p>
          </section>

          <section id="progress" className="manual-section">
            <h2>进度与常见异常</h2>
            <dl className="logic-list compact">
              <div><dt>看不到进度</dt><dd>没有活动任务时全局进度自动隐藏。启动扫描、回测或同步后会重新出现。</dd></div>
              <div><dt>三分钟无变化</dt><dd>界面会提示“可能仍在重计算”。点“查看任务”检查，不要反复启动。</dd></div>
              <div><dt>A 池为空</dt><dd>先看市场环境、数据新鲜度和扫描状态。防守期清空 A 池属于正常门禁。</dd></div>
              <div><dt>回测无法预览</dt><dd>常见原因是股票少于 20 只、未知分组、参数组合超过 5,120 或历史窗口不足。超过 512 但不超过 5,120 只会触发耗时确认。</dd></div>
              <div><dt>分类找不到</dt><dd>当前只开放本地有真实字段的分类。未接入历史成员的数据不会显示。</dd></div>
            </dl>
          </section>

          <section id="boundaries" className="manual-section manual-boundary">
            <h2>系统边界</h2>
            <ul>
              <li>这是个人研究学习平台，不是机构荐股服务，也不是投资建议。</li>
              <li>系统不连接券商，不生成真实订单，真实交易开关保持关闭。</li>
              <li>AI 只解释本地证据，不改变分数、A/B 池或回测结论。</li>
              <li>当前分类是当前快照。历史分类无 PIT 数据时不得宣称行业回测无未来信息。</li>
              <li>失败、证据不足和防守状态必须如实显示，不能改写成通过。</li>
            </ul>
          </section>
        </main>
      </div>
    </div>
  )
}
