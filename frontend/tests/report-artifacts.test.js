import assert from 'node:assert/strict';
import test from 'node:test';
import { reportChartOption, reportOf, reportRows, reportSummary } from '../src/lib/reportArtifacts.js';

function barReport(values) {
  return {
    option: { xAxis: { data: ['地区甲', '地区乙'] }, series: [{ type: 'bar', data: values }] },
  };
}

test('平均量图表显示平均量，合计读取后端本页监测数量', () => {
  const report = {
    ...barReport([5, 20]),
    summary_metric: { label: '本页监测数量合计', value: 120 },
  };
  assert.deepEqual(reportRows(report).map(row => row.value), [5, 20]);
  assert.deepEqual(reportSummary(report), { label: '本页监测数量合计', value: 120 });
});

test('设备概览已有的 summary 不会被当成展示合计', () => {
  const report = { ...barReport([80, 20]), summary: { total_devices: 100, online_devices: 80 } };
  assert.deepEqual(reportSummary(report), { label: '返回分组合计', value: 100 });
});

test('图表平均量为空时仍可展示后端提供的监测合计零值', () => {
  const report = { ...barReport([null, null]), summary_metric: { label: '本页监测数量合计', value: 0 } };
  assert.equal(reportSummary(report).value, 0);
});

function multiSeriesReport() {
  return {
    title: '生境趋势', chart_types: ['line', 'bar'], dimension: { label: '时间', unit: '段', count: 2 },
    report_label: '生境分析', conditions: [], columns: [{ key: 'monitor_count', label: '监测数量' }], rows: [],
    summary_metric: { label: '返回记录监测数量合计', value: 60 },
    option: {
      xAxis: { type: 'category', data: ['2026-01-01', '2026-01-03'] },
      yAxis: { type: 'value', name: '监测数量' },
      legend: { type: 'scroll', data: ['公园', '医院'] },
      series: [
        { name: '公园', type: 'line', data: [10, 30] },
        { name: '医院', type: 'line', data: [20, null] },
      ],
    },
  };
}

test('多系列折线和柱状图保留所有系列、图例和空值', () => {
  const report = multiSeriesReport();
  const original = structuredClone(report);
  for (const type of ['line', 'bar']) {
    const option = reportChartOption(report, type);
    assert.deepEqual(option.series.map(item => item.data), [[10, 30], [20, null]]);
    assert.ok(option.series.every(item => item.type === type && item.connectNulls === false));
    assert.deepEqual(option.legend.data, ['公园', '医院']);
    assert.equal(option.yAxis.name, '监测数量');
  }
  assert.deepEqual(report, original);
  assert.deepEqual(reportRows(report).map(item => item.value), [10, 30, 20, null]);
  assert.equal(reportSummary(report).value, 60);
});

test('饼图和柱状图切换保留原来的单系列数值', () => {
  const report = { ...barReport([80, 20]), chart_types: ['bar', 'pie'], dimension: { label: '蚊种' } };
  const pie = reportChartOption(report, 'pie');
  assert.deepEqual(pie.series[0].data, [{ name: '地区甲', value: 80 }, { name: '地区乙', value: 20 }]);
  const back = reportChartOption({ ...report, option: pie }, 'bar');
  assert.deepEqual(back.series[0].data, [80, 20]);
  assert.deepEqual(back.xAxis.data, ['地区甲', '地区乙']);
});

test('多系列报表历史恢复时检查所有系列而非仅第一组', () => {
  const report = multiSeriesReport();
  const message = { role: 'tool', id: 'report-1', artifact: { artifact_type: 'chart', display_name: report.title, data: report } };
  assert.equal(reportOf(message).option.series.length, 2);
  report.option.series[1].type = 'unknown';
  assert.ok(reportOf(message).error);
});
