import { Icon } from '../Icons.jsx';
import { reportSubtitle } from '../lib/reportArtifacts.js';

export function ReportCard({ report, selected, onOpen }) {
  return <button type="button" className={`report-card${selected ? ' selected' : ''}`} onClick={() => onOpen(report.id)} aria-label={`查看报表：${report.title}`} aria-expanded={selected}>
    <span className="report-card-icon"><Icon name="chart" /></span>
    <span className="report-card-copy"><strong>{report.title}</strong><small>{reportSubtitle(report)}</small><span>图表 · 数据 · 查询条件</span></span>
    <Icon name="arrow" />
  </button>;
}
