const UNITS = { hour: '小时合计', day: '按日', week: '按 ISO 周', month: '按月', year: '按年' };
const SEXES = { 0: '性别未知', 1: '雄性', 2: '雌性' };
// query.monitor_type 是公司接口使用的拉丁名，页面统一展示中文。
const SPECIES = {
  'Aedes albopictus': '白纹伊蚊',
  'Aedes aegypti': '埃及伊蚊',
  'Culex quinquefasciatus': '致倦库蚊',
  'Culex pipiens pallens': '淡色库蚊',
  'Culex tritaeniorhynchus Giles': '三带喙库蚊',
  'Anopheles sinensis Wiedemann': '中华按蚊',
  'Armigeres subalbatus': '骚扰阿蚊',
  Chironomidae: '摇蚊科',
};
const QUERY_LABELS = {
  start_time: '开始时间', end_time: '结束时间', time_unit: '时间粒度',
  province: '省级地区', city: '城市', district: '区县', street: '街道',
  dev_ids: '设备 ID',
};

// 实时消息和历史消息都从 ToolMessage.artifact 读取同一份报表。
export function reportOf(message) {
  if (message.role !== 'tool' || message.additionalKwargs?.hide_from_ui === true) return null;
  const artifact = message.artifact;
  if (artifact?.artifact_type !== 'chart') return null;
  return { id: message.id, title: artifact.display_name, ...artifact.data };
}

function reportSpecies(report) {
  const code = report.query.monitor_type;
  if (code === undefined || code === null) return '全部类别';
  return SPECIES[code] || code;
}

function reportSex(report) {
  const sex = report.query.sex;
  if (sex === undefined || sex === null) return '全部性别';
  return SEXES[sex];
}

export function reportSubtitle(report) {
  const query = report.query;
  const region = [query.province, query.city, query.district, query.street].filter(Boolean).join(' / ');
  const period = [query.start_time?.slice(0, 10), query.end_time?.slice(0, 10)].filter(Boolean).join(' 至 ');
  return [reportSpecies(report), reportSex(report), region || '当前账号可见范围', period].filter(Boolean).join(' · ');
}

export function reportConditions(report) {
  const conditions = [
    { label: '蚊种', value: reportSpecies(report) },
    { label: '性别', value: reportSex(report) },
  ];
  for (const [key, value] of Object.entries(report.query)) {
    if (key === 'monitor_type' || key === 'sex' || value === null) continue;
    const displayValue = key === 'time_unit' ? UNITS[value] : String(value);
    conditions.push({ label: QUERY_LABELS[key] || key, value: displayValue });
  }
  return conditions;
}

// 当前两个报表工具都返回一个数值系列，饼图将名称放在每个数据项里。
export function reportRows(report) {
  const series = report.option.series[0];
  if (series.type === 'pie') return series.data.map(item => ({ label: item.name, value: item.value }));
  return report.option.xAxis.data.map((label, index) => ({ label, value: series.data[index] }));
}

export function reportTypes(report) {
  return report.query.time_unit ? ['line', 'bar'] : ['pie', 'bar'];
}

export const CHART_LABELS = { line: '折线图', bar: '柱状图', pie: '饼图' };
export const formatReportValue = value => value === null || value === undefined ? '—' : value.toLocaleString('zh-CN');

// 只改变已有数据的画法，不更改原始 option，也不发起新查询。
export function reportChartOption(report, type) {
  const rows = reportRows(report);
  const source = report.option;
  const option = {
    color: ['#6e7cf4', '#59b9dc', '#a58bde', '#7cc8b3', '#edb96e', '#e398b5'],
    aria: { enabled: true, description: report.title },
    textStyle: { fontFamily: 'inherit' },
    tooltip: { trigger: type === 'pie' ? 'item' : 'axis', renderMode: 'richText', confine: true },
  };
  const series = { ...source.series[0], type };
  if (report.query.time_unit) series.name = `${reportSpecies(report)}监测数量`;
  if (type === 'pie') {
    option.legend = { type: 'scroll', bottom: 0, itemWidth: 10, itemHeight: 10 };
    series.radius = ['32%', '57%'];
    series.data = rows.map(row => ({ name: row.label, value: row.value }));
  } else {
    delete series.radius;
    option.grid = { left: 60, right: 40, top: 45, bottom: 70 };
    option.xAxis = {
      type: 'category', name: source.xAxis?.name || '蚊种',
      data: rows.map(row => row.label), axisLabel: { hideOverlap: true, color: '#7f8ea7' },
    };
    option.yAxis = { ...source.yAxis, type: 'value', name: '监测数量', min: 0, minInterval: 1 };
    series.data = rows.map(row => row.value);
    series.connectNulls = false;
  }
  option.series = [series];
  return option;
}
