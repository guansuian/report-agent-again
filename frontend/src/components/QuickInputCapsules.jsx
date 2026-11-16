import { useRef, useState } from 'react';
import { Button, Cascader, Checkbox, ConfigProvider, DatePicker, Empty, Popover, Radio, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'dayjs/locale/zh-cn';

// 与后端 species_names.py 中支持的中文名称保持一致。
const SPECIES = ['白纹伊蚊', '埃及伊蚊', '致倦库蚊', '淡色库蚊', '三带喙库蚊', '中华按蚊', '骚扰阿蚊', '摇蚊科'];
const SEXES = ['雄性', '雌性', '未知'];
const TIME_UNITS = ['小时', '天', '周', '月', '年'];
const CAPSULES = [
  { key: 'time', label: '时间' },
  { key: 'species', label: '蚊种' },
  { key: 'sex', label: '性别' },
  { key: 'unit', label: '时间粒度' },
  { key: 'region', label: '地区' },
];

export function QuickInputCapsules({ inputRef, value, onChange, regions }) {
  const [open, setOpen] = useState(null);
  const [dates, setDates] = useState(null);
  const [species, setSpecies] = useState([]);
  const [sex, setSex] = useState('');
  const [timeUnit, setTimeUnit] = useState('');
  const [regionCodes, setRegionCodes] = useState([]);
  const [regionNames, setRegionNames] = useState([]);
  const selection = useRef({ start: 0, end: 0 });

  function changeOpen(key, visible) {
    if (visible) {
      // 弹层会拿走焦点，所以先记住输入框中的光标或选中文字。
      const input = inputRef.current;
      selection.current = { start: input.selectionStart, end: input.selectionEnd };
    }
    setOpen(current => visible ? key : current === key ? null : current);
    if (visible && key === 'region' && !regions.error) void regions.load();
  }

  function insert(text) {
    const { start, end } = selection.current;
    const nextValue = value.slice(0, start) + text + value.slice(end);
    onChange(nextValue);
    setOpen(null);

    // 等 React 更新输入框后，将光标放到刚插入的文字后面。
    requestAnimationFrame(() => {
      const input = inputRef.current;
      const caret = start + text.length;
      input.focus();
      input.setSelectionRange(caret, caret);
    });
  }

  const hasDateRange = Boolean(dates?.[0] && dates?.[1]);
  const textByKey = {
    time: hasDateRange ? `${dates[0].format('YYYY-MM-DD')} 至 ${dates[1].format('YYYY-MM-DD')}` : '',
    species: species.join('、'),
    sex: sex === '未知' ? '性别未知' : sex,
    unit: timeUnit ? `按${timeUnit}统计` : '',
    region: regionNames.join(''),
  };

  let regionControl;
  if (regions.loading) {
    regionControl = <div className="quick-input-region-status" role="status"><Spin size="small" /><span>正在加载地区…</span></div>;
  } else if (regions.error) {
    regionControl = <div className="quick-input-region-status"><span className="error-text" role="alert">{regions.error}</span><Button onClick={() => void regions.load()}>重新加载</Button></div>;
  } else if (regions.tree !== null && regions.tree.length === 0) {
    regionControl = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前账号没有可选地区" />;
  } else {
    regionControl = <Cascader.Panel
      options={regions.tree || []}
      fieldNames={{ value: 'code', label: 'name', children: 'children' }}
      value={regionCodes}
      changeOnSelect
      expandTrigger="click"
      onChange={(codes, selectedOptions) => {
        setRegionCodes(codes);
        setRegionNames(selectedOptions.map(option => option.name));
      }}
    />;
  }

  const controls = {
    time: <DatePicker.RangePicker value={dates} onChange={setDates} format="YYYY-MM-DD" placeholder={['开始日期', '结束日期']} allowClear />,
    species: <Checkbox.Group className="quick-input-species" options={SPECIES} value={species} onChange={setSpecies} />,
    sex: <Radio.Group aria-label="选择性别" options={SEXES} value={sex} onChange={event => setSex(event.target.value)} />,
    unit: <Radio.Group aria-label="选择时间粒度" options={TIME_UNITS} value={timeUnit} onChange={event => setTimeUnit(event.target.value)} />,
    region: <div className="quick-input-region-control">{regionControl}</div>,
  };

  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#8b7ad2', borderRadius: 10, fontSize: 13 } }}>
    <div className="quick-input-capsules" role="group" aria-label="快捷输入">
      {CAPSULES.map(({ key, label }) => <Popover
        key={key}
        trigger="click"
        placement="topLeft"
        arrow={false}
        open={open === key}
        onOpenChange={visible => changeOpen(key, visible)}
        content={<div className={`quick-input-panel${key === 'region' ? ' quick-input-region-panel' : ''}`}>
          <div className="quick-input-heading"><strong>选择{label}</strong><span>{key === 'region' ? '省 / 市 / 区县 / 街道 · 可选到任意一级' : key === 'species' ? '可多选' : key === 'time' ? '日期范围' : '单选'}</span></div>
          {controls[key]}
          {key === 'region' && regionNames.length > 0 && <div className="quick-input-region-selected" aria-live="polite">已选：{regionNames.join(' / ')}</div>}
          <div className="quick-input-footer"><span>插入到输入框光标处</span><Button type="primary" disabled={!textByKey[key]} onClick={() => insert(textByKey[key])}>插入{label}</Button></div>
        </div>}
      >
        <Button className="quick-input-capsule" aria-expanded={open === key}>{label}</Button>
      </Popover>)}
    </div>
  </ConfigProvider>;
}
