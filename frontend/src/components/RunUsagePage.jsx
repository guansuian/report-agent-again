import { useEffect, useId, useMemo, useRef, useState } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { getThread, getThreadTokenUsage, listRuns } from '../api/client.js';
import companyLogo from '../assets/company-logo.svg';
import { Icon } from '../Icons.jsx';
import { formatTime, isActive, STATUS_LABELS } from '../lib/messages.js';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent, CanvasRenderer]);

const SERIES = [
  { key: 'total_tokens', name: '总 Token', color: '#6d7ef2' },
  { key: 'total_input_tokens', name: '输入 Token', color: '#54a9d9' },
  { key: 'total_output_tokens', name: '输出 Token', color: '#c394d9' },
];
const PAGE_SIZE = 10;
const formatCount = value => typeof value === 'number' ? value.toLocaleString('zh-CN') : '—';
// 运行结束才有最终合计，生成中的 0 不应被画成已确认的消耗。
const runValue = (run, key) => isActive(run.status) ? null : typeof run[key] === 'number' ? run[key] : null;

function RunTokenChart({ runs, selectedId, onSelect }) {
  const chart = useRef(null);
  const chartId = useId();
  const selectedIndex = runs.findIndex(run => run.run_id === selectedId);
  const selected = runs[selectedIndex];
  const hasUsage = runs.some(run => SERIES.some(series => runValue(run, series.key) !== null));
  const option = useMemo(() => ({
    aria: { enabled: true, description: '每个数据点代表一次运行的 Token 用量，按创建时间排列。' },
    color: SERIES.map(series => series.color),
    textStyle: { fontFamily: 'inherit' },
    grid: { left: 60, right: 24, top: 60, bottom: 35 },
    legend: { right: 15, top: 8, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { color: '#7f8ea7', fontSize: 11 } },
    tooltip: {
      trigger: 'axis', renderMode: 'richText', confine: true,
      axisPointer: { type: 'line', lineStyle: { color: '#a5b0d3', type: 'dashed' } },
    },
    xAxis: {
      type: 'category', boundaryGap: runs.length === 1,
      data: runs.map((run, index) => `R${index + 1}`),
      axisLine: { lineStyle: { color: '#e9edf5' } }, axisTick: { show: false },
      axisLabel: { color: '#9aa7be', hideOverlap: true },
    },
    yAxis: {
      type: 'value', minInterval: 1,
      axisLabel: { color: '#9aa7be', formatter: value => value >= 1000 ? `${+(value / 1000).toFixed(1)}k` : value },
      splitLine: { lineStyle: { color: '#e9edf5', type: 'dashed' } },
    },
    series: SERIES.map(series => ({
      name: series.name, type: 'line', connectNulls: false, symbolSize: 7,
      lineStyle: { width: 2.5 }, emphasis: { focus: 'series', scale: 1.5 },
      data: runs.map(run => runValue(run, series.key)),
    })),
  }), [runs]);
  const events = useMemo(() => ({
    click: event => { if (runs[event.dataIndex]) onSelect(runs[event.dataIndex].run_id); },
  }), [runs, onSelect]);

  // 点击表格时，同步突出显示图中对应的运行。
  useEffect(() => {
    const instance = chart.current?.getEchartsInstance();
    if (!instance) return;
    instance.dispatchAction({ type: 'downplay' });
    if (selectedIndex >= 0) instance.dispatchAction({ type: 'highlight', dataIndex: selectedIndex });
  }, [selectedIndex, option]);

  function keyboard(event) {
    const next = event.key === 'ArrowRight' ? Math.min(runs.length - 1, selectedIndex + 1)
      : event.key === 'ArrowLeft' ? Math.max(0, selectedIndex - 1)
        : event.key === 'Home' ? 0 : event.key === 'End' ? runs.length - 1 : null;
    if (next !== null) { event.preventDefault(); onSelect(runs[next].run_id); }
  }

  return <section className="usage-chart-card" aria-labelledby={`${chartId}-title`}>
    <div className="usage-card-heading"><div><h2 id={`${chartId}-title`}>每次运行的 Token 变化</h2><p>按创建时间从早到晚排列，每个点代表一次 run。</p></div></div>
    <div className="usage-chart">
      {hasUsage ? <>
        <div tabIndex={0} role="group" aria-label="运行 Token 折线图，使用左右方向键切换运行" onKeyDown={keyboard}>
          <ReactEChartsCore ref={chart} echarts={echarts} option={option} onEvents={events} style={{ height: 320 }} />
        </div>
        {selected && <div className="usage-chart-reading" aria-live="polite"><span><strong>R{selectedIndex + 1}</strong><span>{formatTime(selected.created_at)}</span></span><span>{SERIES.map(series => <span key={series.key}><i style={{ background: series.color }} />{series.name}<strong>{formatCount(runValue(selected, series.key))}</strong></span>)}</span></div>}
      </> : <div className="usage-chart-empty"><Icon name="chart" /><strong>{runs.length ? '运行结束后显示用量曲线' : '还没有运行记录'}</strong><p>{runs.length ? '当前运行的用量仍在统计中。' : '返回对话发送第一条消息，即可开始观察消耗。'}</p></div>}
    </div>
  </section>;
}

export function RunUsagePage({ threadId, userId, onBack, onLogout }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [page, setPage] = useState(0);
  const titleRef = useRef(null);
  useEffect(() => { titleRef.current?.focus(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError('');
      try {
        const thread = await getThread(threadId, userId, controller.signal);
        const [rows, usage] = await Promise.all([
          listRuns(threadId, userId, controller.signal, { limit: 200 }),
          getThreadTokenUsage(threadId, userId, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        const runs = [...rows].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.run_id.localeCompare(b.run_id));
        setData({ thread, runs, usage, updatedAt: new Date() });
        setSelectedId(previous => runs.some(run => run.run_id === previous) ? previous : runs.at(-1)?.run_id ?? null);
        setPage(previous => Math.min(previous, Math.max(0, Math.ceil(runs.length / PAGE_SIZE) - 1)));
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [threadId, userId, refreshVersion]);

  const runs = data?.runs || [];
  const selected = runs.find(run => run.run_id === selectedId);
  const pages = Math.max(1, Math.ceil(runs.length / PAGE_SIZE));
  const tableRows = [...runs].reverse().slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  function selectRun(id) {
    setSelectedId(id);
    const index = runs.findIndex(run => run.run_id === id);
    setPage(Math.floor((runs.length - 1 - index) / PAGE_SIZE));
  }
  function refresh() { setRefreshVersion(value => value + 1); }

  return <div className="run-usage-page">
    <header className="usage-topbar"><div className="usage-nav"><button type="button" className="usage-back" onClick={onBack}><Icon name="arrow-left" />返回对话</button><a href="#" onClick={event => { event.preventDefault(); onBack(); }} aria-label="返回报表对话"><img src={companyLogo} alt="银河星尘" width="136" height="30" /></a></div><div><span className="usage-user">{userId}</span><button type="button" className="text-button" onClick={onLogout}>退出登录</button></div></header>
    <main className="usage-page-content">
      <div className="usage-page-heading"><div><span className="usage-eyebrow">TOKEN USAGE</span><h1 ref={titleRef} tabIndex={-1}>运行用量分析</h1><p>{data?.thread.display_name || '当前会话'}<span className="usage-heading-divider">/</span>观察每次运行的 Token 消耗</p></div><button type="button" className="usage-refresh" disabled={loading} onClick={refresh}><Icon name="refresh" className={loading ? 'is-spinning' : ''} />{loading ? '正在同步' : '刷新数据'}</button></div>
      {error && <div className="usage-page-error" role="alert"><div><strong>{data ? '刷新失败，当前保留上次结果' : '无法加载运行用量'}</strong><p>{error}</p></div><button type="button" className="text-button" disabled={loading} onClick={refresh}>重试</button></div>}
      <div className="usage-overview-label"><h2>会话累计</h2><span>全部已结束运行 · {formatCount(data?.usage.total_runs)} 次</span></div>
      <div className="usage-summary-grid" aria-busy={loading}>
        {[{ key: 'total_tokens', label: '累计总 Token', note: '输入与输出的合计', icon: 'coins' }, { key: 'total_input_tokens', label: '输入 Token', note: '提示词、历史和工具结果', icon: 'input' }, { key: 'total_output_tokens', label: '输出 Token', note: '模型生成的内容', icon: 'output' }, { key: 'llm_call_count', label: '模型调用次数', note: '已返回有效用量的调用', icon: 'model' }].map(metric => <section className={`usage-summary-card ${metric.icon}`} key={metric.key}><div><span>{metric.label}</span><Icon name={metric.icon} /></div><strong>{formatCount(data?.usage[metric.key])}</strong><p>{metric.note}</p></section>)}
      </div>
      {loading && !data ? <div className="usage-page-loading" role="status"><span className="spinner" />正在读取用量和运行记录…</div> : data && <>
        <RunTokenChart runs={runs} selectedId={selectedId} onSelect={selectRun} />
        <section className="usage-table-card" aria-labelledby="usage-runs-title"><div className="usage-card-heading"><div><h2 id="usage-runs-title">运行明细</h2><p>最近 {runs.length} 次运行，最新的排在前面。点击序号与折线图联动查看。</p></div><span className="usage-record-count">{runs.length} 条记录</span></div>
          {runs.length ? <><div className="usage-table-scroll" tabIndex={0} role="region" aria-label="运行 Token 用量明细，可横向滚动"><table className="usage-table"><thead><tr><th>图内序号</th><th>创建时间</th><th>状态</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th><th>模型调用</th></tr></thead><tbody>{tableRows.map(run => { const index = runs.indexOf(run); return <tr key={run.run_id} className={selectedId === run.run_id ? 'selected' : ''}><td><button type="button" className="usage-run-select" aria-pressed={selectedId === run.run_id} onClick={() => selectRun(run.run_id)} title={run.run_id}>R{index + 1}<small>{run.run_id.slice(0, 8)}</small></button></td><td>{formatTime(run.created_at)}</td><td><span className={`status-badge ${run.status}`}>{STATUS_LABELS[run.status] || run.status}</span></td>{['total_input_tokens', 'total_output_tokens', 'total_tokens', 'llm_call_count'].map(key => <td className={key === 'total_tokens' ? 'usage-table-total' : ''} key={key}>{isActive(run.status) ? '统计中' : formatCount(run[key])}</td>)}</tr>; })}</tbody></table></div><div className="usage-table-footer"><span>第 {page + 1} / {pages} 页</span><div><button type="button" className="text-button" disabled={!page} onClick={() => setPage(value => value - 1)}><Icon name="arrow-left" />上一页</button><button type="button" className="text-button" disabled={page >= pages - 1} onClick={() => setPage(value => value + 1)}>下一页<Icon name="arrow" /></button></div></div></> : <p className="empty-state">当前会话还没有运行记录。</p>}
          {selected && <div className="usage-selected-detail"><span>当前选中 <strong>R{runs.indexOf(selected) + 1}</strong></span><span className="identifier">{selected.run_id}</span>{selected.error && <p className="error-text">{selected.error}</p>}</div>}
        </section>
      </>}
      <footer className="usage-page-note"><span><Icon name="help" />曲线与明细最多展示最近 200 次 run；会话累计包含全部已结束运行。一次 run 可以调用模型多次。</span><span>{data ? `更新于 ${data.updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : 'Token 数量不代表金额'}</span></footer>
    </main>
  </div>;
}
