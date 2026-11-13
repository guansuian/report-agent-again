import { useEffect, useMemo, useRef } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { reportChartOption } from '../lib/reportArtifacts.js';

echarts.use([BarChart, LineChart, PieChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export default function ReportChart({ report, type, enlarged = false }) {
  const container = useRef(null);
  const chart = useRef(null);
  const option = useMemo(() => reportChartOption(report, type), [report, type]);

  function resizeChart() {
    const instance = chart.current?.getEchartsInstance();
    if (instance && container.current.clientWidth > 0) {
      instance.resize({ width: 'auto', height: 'auto' });
    }
  }

  useEffect(() => {
    // 面板展开和窗口缩放都会改变容器宽度，让画布随实际尺寸更新。
    const observer = new ResizeObserver(resizeChart);
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  return <div ref={container} style={{ width: '100%' }}><ReactEChartsCore ref={chart} echarts={echarts} option={option} notMerge autoResize={false} onChartReady={resizeChart} style={{ height: enlarged ? '55vh' : 340, width: '100%' }} /></div>;
}
