import { useId, useState } from 'react';
import { ConfigProvider, DatePicker } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { Icon } from '../Icons.jsx';
import {
  buildFormSubmissionValue, buildFormSummary, buildInitialFormValues,
  createClarificationResponse, findMissingRequiredFields, parseFormFields, restoreFormValues,
} from '../lib/humanInput.js';

// 单个表单字段的输入控件。checkbox 由调用方单独渲染（它的 label 布局不一样）。
// 7 种类型：text / textarea / number / select / multi_select / checkbox / date。
function FormFieldInput({ field, value, disabled, id, onChange }) {
  const stringValue = typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  if (field.type === 'textarea') {
    return <textarea id={id} rows="2" placeholder={field.placeholder} value={stringValue} disabled={disabled} onChange={event => onChange(event.target.value)} />;
  }
  if (field.type === 'date') {
    // DatePicker 使用 dayjs 对象显示日期；提交仍使用 YYYY-MM-DD 字符串。
    const selectedDate = stringValue ? dayjs(stringValue) : null;
    return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#8b7ad2', borderRadius: 10, fontSize: 13 } }}>
      <DatePicker id={id} className="clarification-date-picker" value={selectedDate} format="YYYY-MM-DD" placeholder={field.placeholder || '请选择日期'} disabled={disabled} onChange={(_, dateString) => onChange(dateString)} />
    </ConfigProvider>;
  }
  if (field.type === 'select') {
    return <select id={id} value={stringValue} disabled={disabled} onChange={event => onChange(event.target.value)}>
      <option value="" disabled>{field.placeholder || '请选择…'}</option>
      {(field.options || []).map(option => <option key={option.id} value={option.value}>{option.label}</option>)}
    </select>;
  }
  if (field.type === 'multi_select') {
    const selected = Array.isArray(value) ? value : [];
    return <div className="clarification-multi-select" role="group">
      {(field.options || []).map(option => {
        const on = selected.includes(option.value);
        return <button type="button" key={option.id} className={`multi-option${on ? ' on' : ''}`} disabled={disabled} aria-pressed={on} onClick={() => onChange(on ? selected.filter(item => item !== option.value) : [...selected, option.value])}>{option.label}</button>;
      })}
    </div>;
  }
  // text / number 共用一个单行输入框
  return <input id={id} type={field.type === 'number' ? 'number' : 'text'} placeholder={field.placeholder} value={stringValue} disabled={disabled} onChange={event => onChange(event.target.value)} />;
}

export function ClarificationCard({ request, answer, active, disabled, onAnswer }) {
  const id = useId();
  const [selected, setSelected] = useState('');
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');
  const options = request.options || [];
  const option = options.find(item => item.id === selected);
  const ready = Boolean(option || text.trim()) && active && !answer && !disabled && !submitting;
  const locked = disabled || submitting || !active || Boolean(answer);

  // v2 表单：解析字段；字段坏了（或为空）就退回文本输入，不让卡片画不出来。
  const isForm = request.input_mode === 'form';
  const fields = isForm ? (parseFormFields(request.fields) || []) : [];
  const showForm = isForm && fields.length > 0;
  const [formValues, setFormValues] = useState(() => buildInitialFormValues(fields));
  const restoredValues = answer && showForm ? restoreFormValues(fields, answer.value) : null;
  const displayedValues = restoredValues || formValues;
  const answerText = restoredValues ? buildFormSummary(fields, restoredValues) : answer?.value;

  function changeField(name, value) {
    setFormError('');
    setFormValues(previous => ({ ...previous, [name]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    try { await onAnswer(createClarificationResponse(request, text, option)); }
    finally { setSubmitting(false); }
  }

  async function submitForm(event) {
    event.preventDefault();
    if (locked) return;
    const missing = findMissingRequiredFields(fields, formValues);
    if (missing.length) { setFormError('请填写必填项：' + missing.map(field => field.label).join('、')); return; }
    if (!buildFormSummary(fields, formValues).trim()) { setFormError('请至少填写一项后再提交。'); return; }
    setSubmitting(true);
    try { await onAnswer(createClarificationResponse(request, buildFormSubmissionValue(fields, formValues))); }
    finally { setSubmitting(false); }
  }

  return <section className={`clarification-card${answer ? ' answered' : ''}`} aria-labelledby={`${id}-question`}>
    <div className="clarification-heading"><span className="clarification-icon"><Icon name="chat" /></span><span>补充一点信息</span><span className={`clarification-state${answer ? ' done' : ''}`}>{answer ? answer.pending ? '正在提交' : '已回答' : active ? '等待你的回答' : '待回答'}</span></div>
    <h2 id={`${id}-question`}>{request.question}</h2>
    {request.context && <p className="clarification-context">{request.context}</p>}
    {showForm ? <form className="clarification-form" onSubmit={submitForm}>
      <fieldset disabled={locked}>
        <legend className="sr-only">填写表单补充信息</legend>
        {fields.map(field => field.type === 'checkbox' ? <label className="clarification-checkbox" key={field.name}>
          <input type="checkbox" checked={displayedValues[field.name] === true} disabled={locked} onChange={event => changeField(field.name, event.target.checked)} />
          <span>{field.label}{field.required ? <em className="required-star">*</em> : null}</span>
        </label> : <label className="clarification-field" key={field.name}>
          <span className="clarification-field-label">{field.label}{field.required ? <em className="required-star">*</em> : null}</span>
          <FormFieldInput field={field} id={`${id}-${field.name}`} value={displayedValues[field.name]} disabled={locked} onChange={value => changeField(field.name, value)} />
        </label>)}
      </fieldset>
      {!answer && formError && <p className="clarification-form-error" role="alert">{formError}</p>}
      {answer && <div className="clarification-answer"><span>{answer.pending ? '提交的回答' : '你的回答'}</span><strong>{answerText}</strong></div>}
      <div className="clarification-footer"><span>{answer ? answer.pending ? '正在提交你的回答' : '回答已保存' : disabled ? '当前任务结束后即可提交' : active ? '提交后，助手会接着处理' : '请先回答最新的问题'}</span><button className="primary clarification-submit" type="submit" disabled={locked}>{answer ? answer.pending ? '正在提交…' : '已提交' : submitting ? '正在提交…' : '提交并继续'}<Icon name="arrow" /></button></div>
    </form> : answer ? <div className="clarification-answer"><span>{answer.pending ? '提交的回答' : '你的回答'}</span><strong>{answer.value}</strong></div> : <form onSubmit={submit}>
      <fieldset disabled={locked}>
        <legend className="sr-only">选择一个选项，或填写自己的回答</legend>
        {!!options.length && <div className="clarification-options">{options.map(item => <label className={`clarification-option${selected === item.id ? ' selected' : ''}`} key={item.id}><input type="radio" name={`${id}-option`} value={item.id} checked={selected === item.id} onChange={() => { setSelected(item.id); setText(''); }} /><span>{item.label}</span></label>)}</div>}
        <label className="clarification-input-label" htmlFor={`${id}-answer`}>{options.length ? '也可以填写其他回答' : '你的回答'}</label>
        <textarea id={`${id}-answer`} rows="2" placeholder={options.length ? '没有合适的选项？在这里补充…' : '请在这里填写…'} value={text} onChange={event => { setText(event.target.value); setSelected(''); }} />
      </fieldset>
      <div className="clarification-footer"><span>{disabled ? '当前任务结束后即可提交' : active ? '提交后，助手会接着处理' : '请先回答最新的问题'}</span><button className="primary clarification-submit" type="submit" disabled={!ready}>{submitting ? '正在提交…' : '提交并继续'}<Icon name="arrow" /></button></div>
    </form>}
  </section>;
}
