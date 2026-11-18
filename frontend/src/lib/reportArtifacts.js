// 实时消息和历史消息都从 ToolMessage.artifact 读取同一份报表。
export function reportOf(message) {
  if (message.role !== 'tool' || message.additionalKwargs?.hide_from_ui === true) return null;
  const artifact = message.artifact;
  if (artifact?.artifact_type !== 'chart') return null;
  const data = artifact.data;
  const hasDimension = typeof data?.dimension?.label === 'string' && typeof data.dimension.unit === 'string';
  const hasColumns = Array.isArray(data?.columns) && data.columns.length > 0 && data.columns.every(column => typeof column?.key === 'string' && typeof column.label === 'string');
  const hasChartTypes = Array.isArray(data?.chart_types) && data.chart_types.length > 0 && data.chart_types.every(type => ['line', 'bar', 'pie'].includes(type));
  const hasConditions = Array.isArray(data?.conditions) && data.conditions.every(item => typeof item?.label === 'string' && typeof item.value === 'string');
  // 只接受新格式。历史产物缺少展示信息时明确提示，不猜测表头或报表类型。
  if (typeof data?.report_label !== 'string' || !hasDimension || !hasColumns || !hasChartTypes || !hasConditions || !Array.isArray(data.rows) || !data.chart_types.includes(data.option?.series?.[0]?.type)) {
    return { id: message.id, title: artifact.display_name, error: '报表格式已更新，请重新查询生成报表。' };
  }
  return { id: message.id, title: artifact.display_name, ...data };
}

export function reportSubtitle(report) {
  // 副标题紧凑展示条件值，完整标签在查询条件面板显示。
  return report.conditions.map(item => item.value).join(' · ');
}

export function reportConditions(report) {
  return report.conditions;
}

// 读取图表中的数值系列，用于统计和切换图形；表格直接使用 columns 和 rows。
export function reportRows(report) {
  const series = report.option.series[0];
  if (series.type === 'pie') return series.data.map(item => ({ label: item.name, value: item.value }));
  return report.option.xAxis.data.map((label, index) => ({ label, value: series.data[index] }));
}

export function reportTypes(report) {
  return report.chart_types;
}

export const CHART_LABELS = { line: '折线图', bar: '柱状图', pie: '饼图' };
export const formatReportValue = value => value === null || value === undefined ? '—' : value.toLocaleString('zh-CN');

// 只改变已有数据的画法，不更改原始 option，也不发起新查询。
export function reportChartOption(report, type) {
  if (!report.chart_types.includes(type)) throw new Error('该报表不支持此图表类型。');
  const rows = reportRows(report);
  const source = report.option;
  const option = {
    color: ['#6e7cf4', '#59b9dc', '#a58bde', '#7cc8b3', '#edb96e', '#e398b5'],
    aria: { enabled: true, description: report.title },
    textStyle: { fontFamily: 'inherit' },
    tooltip: { trigger: type === 'pie' ? 'item' : 'axis', renderMode: 'richText', confine: true },
  };
  const series = { ...source.series[0], type };
  if (type === 'pie') {
    option.legend = { type: 'scroll', bottom: 0, itemWidth: 10, itemHeight: 10 };
    series.radius = ['32%', '57%'];
    series.data = rows.map(row => ({ name: row.label, value: row.value }));
  } else {
    delete series.radius;
    option.grid = { left: 60, right: 40, top: 45, bottom: 70 };
    option.xAxis = {
      type: 'category', name: report.dimension.label,
      data: rows.map(row => row.label), axisLabel: { hideOverlap: true, color: '#7f8ea7' },
    };
    option.yAxis = { ...source.yAxis, type: 'value', name: series.name, min: 0, minInterval: 1 };
    series.data = rows.map(row => row.value);
    series.connectNulls = false;
  }
  option.series = [series];
  return option;
}
