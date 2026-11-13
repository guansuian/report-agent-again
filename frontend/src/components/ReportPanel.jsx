import { lazy, Suspense, useEffect, useId, useRef, useState } from 'react';
import { Icon } from '../Icons.jsx';
import { CHART_LABELS, formatReportValue, reportConditions, reportRows, reportSubtitle, reportTypes } from '../lib/reportArtifacts.js';

const ReportChart = lazy(() => import('./ReportChart.jsx'));

function ReportTable({ report }) {
  const rows = reportRows(report);
  return <div className="report-table-wrap"><table className="report-table">
    <caption>{report.title}，共 {rows.length} 个分组</caption>
    <thead><tr><th scope="col">{report.query.time_unit ? '时间分组' : '蚊种'}</th><th scope="col">监测数量</th></tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}><th scope="row">{row.label}</th><td>{formatReportValue(row.value)}</td></tr>)}</tbody>
  </table></div>;
}

function ReportView({ report, view, type, enlarged = false }) {
  return view === 'table' ? <ReportTable report={report} /> : <Suspense fallback={<div className="report-loading" role="status">正在加载图表…</div>}><ReportChart report={report} type={type} enlarged={enlarged} /></Suspense>;
}

function EnlargedReport({ report, view, type, onClose }) {
  const dialog = useRef(null);
  const heading = useId();
  useEffect(() => { dialog.current.showModal(); }, []);
  return <dialog ref={dialog} className="report-dialog" aria-labelledby={heading} onCancel={onClose}>
    <div className="report-panel-heading"><div><span className="report-eyebrow">报表预览</span><h2 id={heading}>{report.title}</h2></div><button className="icon-button" aria-label="关闭放大报表" onClick={onClose}><Icon name="close" /></button></div>
    <p className="report-subtitle">{reportSubtitle(report)}</p>
    <ReportView report={report} view={view} type={type} enlarged />
    <p className="report-note">{report.note}</p>
  </dialog>;
}

function ReportContent({ report, active, expanded, view, type, onView, onType }) {
  const [enlarged, setEnlarged] = useState(false);
  const rows = reportRows(report);
  const hasPositiveValue = rows.some(row => row.value > 0);
  const total = rows.every(row => typeof row.value === 'number') ? rows.reduce((sum, row) => sum + row.value, 0) : null;
  useEffect(() => { if (!active || !expanded) setEnlarged(false); }, [active, expanded]);
  return <>
    <p className="report-subtitle">{reportSubtitle(report)}</p>
    <div className="report-metrics"><div><span>返回分组合计</span><strong>{formatReportValue(total)}</strong></div><div><span>{report.query.time_unit ? '时间分组' : '蚊种类别'}</span><strong>{rows.length}<small> 组</small></strong></div></div>
    <section className="report-visual" aria-label="报表展示">
      <div className="report-view-toolbar"><div className="report-segments" aria-label="展示方式">{[['chart', '图表'], ['table', '数据表格']].map(([value, label]) => <button type="button" key={value} className={view === value ? 'active' : ''} aria-pressed={view === value} onClick={() => onView(value)}>{label}</button>)}</div><button type="button" className="text-button" onClick={() => setEnlarged(true)}><Icon name="expand" />放大</button></div>
      {view === 'chart' && <div className="report-chart-types" aria-label="图表类型">{reportTypes(report).map(value => <button type="button" key={value} className={type === value ? 'active' : ''} aria-pressed={type === value} disabled={value === 'pie' && !hasPositiveValue} title={value === 'pie' && !hasPositiveValue ? '没有正数数据时无法展示占比' : undefined} onClick={() => onType(value)}>{CHART_LABELS[value]}</button>)}</div>}
      <ReportView report={report} view={view} type={type} />
    </section>
    <p className="report-local-hint">切换图表或表格只改变展示，不会重新查询。</p>
    <details className="report-conditions"><summary>查询条件</summary><dl>{reportConditions(report).map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></details>
    <div className="report-notes"><strong>数据说明</strong>{[report.scope, report.ratio_scope, report.note, report.filter_note].filter(Boolean).map((note, index) => <p key={index}>{note}</p>)}</div>
    {enlarged && active && expanded && <EnlargedReport report={report} view={view} type={type} onClose={() => setEnlarged(false)} />}
  </>;
}

function ReportListItem({ report, number, expanded, onToggle, onQuestion, active }) {
  const contentId = useId();
  const toggleButton = useRef(null);
  const [view, setView] = useState('chart');
  const [type, setType] = useState(report.option.series[0].type);
  // 收起动画结束后再卸载图表，避免瞬间消失，也不让所有报表一直占用绘图资源。
  const [keepContent, setKeepContent] = useState(expanded);
  useEffect(() => {
    if (!expanded) return;
    setKeepContent(true);
    toggleButton.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [expanded]);
  return <section className={`report-list-item${expanded ? ' expanded' : ''}`}>
    <button ref={toggleButton} type="button" className="report-list-toggle" aria-expanded={expanded} aria-controls={contentId} onClick={onToggle}>
      <span className="report-list-number">{String(number).padStart(2, '0')}</span>
      <span className="report-list-label"><strong>{report.title}</strong><small>{report.query.time_unit ? '时间趋势' : '种类构成'} · {report.query.city || report.query.province || '当前账号可见范围'}</small></span>
      <Icon name="chevron-down" />
    </button>
    <div id={contentId} className="report-list-collapse" inert={!expanded} onTransitionEnd={event => {
      if (event.target === event.currentTarget && event.propertyName === 'grid-template-rows' && !expanded) setKeepContent(false);
    }}><div className="report-list-clip">{(expanded || keepContent) && <div className="report-list-content">
      <ReportContent report={report} active={active} expanded={expanded} view={view} type={type} onView={setView} onType={setType} />
      <button type="button" className="report-question" onClick={() => onQuestion(report)}><Icon name="chat" />基于此报表提问<Icon name="arrow" /></button>
    </div>}</div></div>
  </section>;
}

export function ReportPanel({ reports, selectedId, onSelect, onClose, onQuestion, active }) {
  return <aside className="report-panel" aria-label="右侧报表区">
    <div className="report-panel-heading"><div><span className="report-eyebrow">报表工作区</span><h2>产物列表 <small>{reports.length}</small></h2></div><button className="icon-button" aria-label="关闭报表区" onClick={onClose}><Icon name="close" /></button></div>
    <p className="report-list-hint">点击展开报表，再次点击收起</p>
    <div className="report-panel-scroll">{reports.map((report, index) => <ReportListItem key={report.id} report={report} number={index + 1} expanded={report.id === selectedId} onToggle={() => onSelect(report.id)} onQuestion={onQuestion} active={active} />)}</div>
  </aside>;
}
