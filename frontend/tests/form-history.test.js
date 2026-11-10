import test from 'node:test';
import assert from 'node:assert/strict';
import { buildFormSubmissionValue, buildFormSummary, clarificationState, createClarificationResponse, parseFormFields, restoreFormValues } from '../src/lib/humanInput.js';
import { groupMessagesForDisplay, historyMessages } from '../src/lib/messages.js';

const fields = parseFormFields([
  { name: 'name', label: '名称', type: 'text' },
  { name: 'notes', label: '说明', type: 'textarea' },
  { name: 'limit', label: '条数', type: 'number' },
  { name: 'metric', label: '指标', type: 'select', options: [{ id: 'sales', label: '销售额', value: 'sales' }] },
  { name: 'dimensions', label: '维度', type: 'multi_select', options: [{ id: 'day', label: '按天', value: 'day' }, { id: 'region', label: '地区', value: 'region' }] },
  { name: 'compare', label: '对比', type: 'checkbox' },
]);

test('表单回答经过历史保存和读取后，六种控件的值都能恢复且回答气泡保持隐藏', () => {
  const values = { name: '测试报表', notes: '保留填写内容', limit: '0', metric: 'sales', dimensions: ['day', 'region'], compare: true };
  const request = { version: 2, kind: 'human_input_request', source: 'ask_clarification', request_id: 'form1', question: '请填写查询条件', input_mode: 'form', fields };
  const response = createClarificationResponse(request, buildFormSubmissionValue(fields, values));
  const messages = historyMessages([
    { seq: 1, event_type: 'llm.tool.result', content: { id: 'form1', type: 'tool', content: request.question, artifact: { human_input: request } } },
    { seq: 2, event_type: 'llm.human.input', content: { id: 'answer1', type: 'human', content: response.value, additional_kwargs: { hide_from_ui: true, human_input_response: response } } },
  ]);
  const answer = clarificationState(messages).answers.get('form1');
  assert.deepEqual(restoreFormValues(fields, answer.value), values);
  assert.equal(clarificationState(messages).openRequest, null);
  assert.equal(groupMessagesForDisplay(messages).some(group => group.role === 'user'), false);
  assert.equal(buildFormSummary(fields, restoreFormValues(fields, answer.value)).includes('[values:'), false);
});

test('恢复时保留未勾选、零和未填写字段，摘要不包含字段 JSON', () => {
  const values = { limit: 0, dimensions: [], compare: false };
  const restored = restoreFormValues(fields, buildFormSubmissionValue(fields, values));
  assert.deepEqual(restored, { limit: 0, compare: false });
  assert.equal(Object.hasOwn(restored, 'name'), false);
  assert.equal(buildFormSummary(fields, restored), '条数: 0; 对比: 否');
});

test('用户文字含括号、分号或 values 标记也能正确恢复', () => {
  const values = { notes: '说明; [values: {"sample": true}]，以及 [其他] 内容', compare: false };
  assert.deepEqual(restoreFormValues(fields, buildFormSubmissionValue(fields, values)), values);
});

test('普通聊天回答和损坏 JSON 不会伪造表单值，未知字段不会写入控件', () => {
  assert.equal(restoreFormValues(fields, '先不填写，继续聊聊'), null);
  assert.equal(restoreFormValues(fields, '摘要 [values: {错误}]'), null);
  assert.deepEqual(restoreFormValues(fields, '摘要 [values: {"name":"已填写","unknown":"忽略","compare":false}]'), { name: '已填写', compare: false });
});
