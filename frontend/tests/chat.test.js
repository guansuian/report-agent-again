import test from 'node:test';
import assert from 'node:assert/strict';
import { readSSE, streamRun, listMessages, getRun, listRuns } from '../src/api/client.js';
import { applyStreamEvent, groupMessagesForDisplay, historyMessages } from '../src/lib/messages.js';
import { clarificationOf, clarificationState, createClarificationResponse } from '../src/lib/humanInput.js';

function responseFromParts(parts) {
  return new Response(new ReadableStream({ start(controller) { for (const part of parts) controller.enqueue(part); controller.close(); } }));
}

test('SSE 在任意字节处断开时仍能还原中文、CRLF 和多个事件', async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\nevent: messages\r\nid: 1\r\ndata: [{"type":"AIMessageChunk","content":"销售额"},{}]\r\n\r\nevent: end\ndata: null\n\n');
  const entries = [];
  await readSSE(responseFromParts([...bytes].map(byte => Uint8Array.of(byte))), entry => entries.push(entry));
  assert.equal(entries.length, 2);
  assert.equal(entries[0].id, '1');
  assert.equal(entries[0].data[0].content, '销售额');
  assert.equal(entries[1].event, 'end');
});

test('流未收到 end 就断开时，提示同步记录，不假装成功', async () => {
  await assert.rejects(readSSE(responseFromParts([new TextEncoder().encode('event: metadata\ndata: {"run_id":"r1"}\n\n')]), () => {}), /连接已断开/);
});

test('messages 增量和 values 完整快照合并成一个气泡，保留工具结果', () => {
  let messages = [{ id: 'u1', role: 'user', text: '查销售额' }];
  messages = applyStreamEvent(messages, { event: 'messages', data: [{ id: 'a1', type: 'AIMessageChunk', content: '销售额' }, {}] }, 'r1');
  messages = applyStreamEvent(messages, { event: 'messages', data: [{ id: 'a1', type: 'AIMessageChunk', content: ' 120 万' }, {}] }, 'r1');
  assert.equal(messages[1].text, '销售额 120 万');
  messages = applyStreamEvent(messages, { event: 'values', data: { messages: [{ id: 'u1', type: 'human', content: '查销售额' }, { id: 't1', type: 'tool', name: 'query_report', content: '120 万' }, { id: 'a1', type: 'ai', content: '销售额 120 万。' }] } }, 'r1');
  assert.equal(messages.filter(message => message.id === 'a1').length, 1);
  assert.equal(messages.find(message => message.id === 'a1').text, '销售额 120 万。');
  assert.equal(messages.find(message => message.id === 't1').role, 'tool');
});

test('无 id 的临时增量在完整快照到达时移除', () => {
  let messages = applyStreamEvent([], { event: 'messages', data: [{ type: 'AIMessageChunk', content: '你好' }, {}] }, 'r1');
  messages = applyStreamEvent(messages, { event: 'values', data: { messages: [{ id: 'final', type: 'ai', content: '你好。' }] } }, 'r1');
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, '你好。');
});

test('历史记录兼容字符串和 LangChain 消息对象，保留排序编号', () => {
  const messages = historyMessages([
    { seq: 1, run_id: 'r1', event_type: 'llm.human.input', content: '销售额' },
    { seq: 2, run_id: 'r1', event_type: 'llm.tool.result', content: { type: 'tool', name: 'query_report', content: '120 万' } },
    { seq: 3, run_id: 'r1', event_type: 'llm.ai.response', content: { id: 'a1', type: 'ai', content: [{ type: 'text', text: '<script>不会执行</script>' }] } },
  ]);
  assert.deepEqual(messages.map(message => message.role), ['user', 'tool', 'assistant']);
  assert.equal(messages[2].text, '<script>不会执行</script>');
  assert.equal(messages[2].seq, 3);
});

test('四个接口的 URL、POST 输入和流式模式与后端契约一致', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (path, options = {}) => {
    calls.push({ path, options });
    return path.endsWith('/stream') ? responseFromParts([new TextEncoder().encode('event: end\ndata: null\n\n')]) : Response.json([]);
  });
  await listMessages('t1', { userId: 'alice', before: 51 });
  await listRuns('t1', 'alice');
  await getRun('r1', 'alice');
  await streamRun('t1', { id: 'u1', role: 'user', content: '销售额' }, () => {}, 'alice');
  assert.equal(calls[0].path, '/api/threads/t1/messages?user_id=alice&limit=50&before_seq=51');
  assert.equal(calls[1].path, '/api/threads/t1/runs?user_id=alice&limit=50');
  assert.equal(calls[2].path, '/api/runs/r1?user_id=alice');
  assert.equal(calls[3].options.method, 'POST');
  const body = JSON.parse(calls[3].options.body);
  assert.equal(body.user_id, 'alice');
  assert.deepEqual(body.stream_mode, ['values', 'messages-tuple']);
  assert.equal(body.input.messages[0].id, 'u1');
});

test('后端返回 HTTP 错误时显示错误，不进入假回复', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '数据库连接失败' }, { status: 503 }));
  await assert.rejects(streamRun('t1', { role: 'user', content: '销售额' }, () => {}, 'alice'), /数据库连接失败/);
});

const question = { version: 1, kind: 'human_input_request', source: 'ask_clarification', request_id: 'clarification:c1', question: '你想查询哪个指标？', input_mode: 'choice_with_other', options: [{ id: 'option-1', label: '销售额', value: 'sales' }] };
const questionMessage = { id: question.request_id, type: 'tool', name: 'ask_clarification', content: question.question, artifact: { human_input: question } };

test('同轮说明文字和追问卡片归为一条 AI 回复，用户回答后另起一轮', () => {
  const messages = [
    { id: 'u1', role: 'user', text: '帮我查报表' },
    { id: 'a1', role: 'assistant', text: '先补充一个信息。' },
    { id: 'q1', role: 'tool', artifact: { human_input: question } },
    { id: 'u2', role: 'user', text: '销售额' },
    { id: 'a2', role: 'assistant', text: '我来查询。' },
    { id: 't2', role: 'tool', text: '123 万元' },
    { id: 'a3', role: 'assistant', text: '查询完成。' },
  ];
  const groups = groupMessagesForDisplay(messages);
  assert.deepEqual(groups.map(group => group.role), ['user', 'assistant', 'user', 'assistant']);
  assert.deepEqual(groups[1].messages.map(message => message.id), ['a1', 'q1']);
  assert.deepEqual(groups[3].messages.map(message => message.id), ['a2', 't2', 'a3']);
  assert.equal(groups[1].messages[1], messages[2]);
});

test('隐藏的用户回答仍分隔回复轮次，隐藏工具消息不显示', () => {
  const groups = groupMessagesForDisplay([
    { id: 'a1', role: 'assistant', text: '请补充。' },
    { id: 't1', role: 'tool', additionalKwargs: { hide_from_ui: true } },
    { id: 'u2', role: 'user', text: '销售额', additionalKwargs: { hide_from_ui: true } },
    { id: 'a2', role: 'assistant', text: '收到。' },
  ]);
  assert.deepEqual(groups.map(group => group.messages.map(message => message.id)), [['a1'], ['a2']]);
});

test('追问从实时快照和数据库历史读取后保留相同结构，刷新仍可回答', () => {
  const live = applyStreamEvent([], { event: 'values', data: { messages: [questionMessage] } }, 'r1');
  const restored = historyMessages([{ seq: 2, run_id: 'r1', event_type: 'llm.tool.result', content: questionMessage }]);
  assert.deepEqual(clarificationOf(live[0]), clarificationOf(restored[0]));
  assert.equal(clarificationState(restored).openRequest.request_id, question.request_id);
});

test('回答携带问题 ID，重新读取历史后卡片恢复为已回答', () => {
  const response = createClarificationResponse(question, '', question.options[0]);
  const messages = historyMessages([
    { seq: 2, event_type: 'llm.tool.result', content: questionMessage },
    { seq: 3, event_type: 'llm.human.input', content: { id: 'u2', content: response.value, additional_kwargs: { human_input_response: response } } },
  ]);
  const state = clarificationState(messages);
  assert.equal(state.openRequest, null);
  assert.equal(state.answers.get(question.request_id).response_kind, 'option');
  assert.equal(state.answers.get(question.request_id).option_id, 'option-1');
  assert.equal(state.answers.get(question.request_id).pending, false);
});

test('卡片回答隐藏气泡，但实时状态和刷新后的历史仍能认出已回答', () => {
  const response = createClarificationResponse(question, '', question.options[0]);
  const userMessage = {
    id: 'hidden-answer', type: 'human', content: response.value,
    additional_kwargs: { hide_from_ui: true, human_input_response: response },
  };
  const optimistic = [
    { id: question.request_id, role: 'tool', artifact: questionMessage.artifact },
    { id: userMessage.id, role: 'user', text: userMessage.content, additionalKwargs: userMessage.additional_kwargs, pending: true },
  ];
  assert.equal(clarificationState(optimistic).answers.get(question.request_id).pending, true);
  const live = applyStreamEvent(optimistic, { event: 'values', data: { messages: [questionMessage, userMessage] } }, 'r2');
  const restored = historyMessages([
    { seq: 1, event_type: 'llm.tool.result', content: questionMessage },
    { seq: 2, event_type: 'llm.human.input', content: userMessage },
  ]);
  for (const messages of [live, restored]) {
    const state = clarificationState(messages);
    assert.equal(state.openRequest, null);
    assert.equal(state.answers.get(question.request_id).value, 'sales');
    assert.equal(state.answers.get(question.request_id).pending, false);
    assert.equal(messages.length, 2); // 回答数据保留给状态处理，只在展示时隐藏。
    assert.equal(groupMessagesForDisplay(messages).some(group => group.role === 'user'), false);
  }
});

test('纯文字回答只关闭最近一个问题，隐藏消息不会误关闭追问', () => {
  const q1 = { id: 'q1', role: 'tool', artifact: { human_input: question } };
  const q2 = { id: 'q2', role: 'tool', artifact: { human_input: { ...question, request_id: 'q2' } } };
  let messages = [q1, q2, { role: 'user', text: '隐藏的消息', additionalKwargs: { hide_from_ui: true } }];
  assert.equal(clarificationState(messages).openRequest.request_id, 'q2');
  messages.push({ role: 'user', text: '订单数' });
  const state = clarificationState(messages);
  assert.equal(state.openRequest.request_id, question.request_id);
  assert.equal(state.answers.get('q2').value, '订单数');
});

test('确认快照清除本机提交中标记，相同 ID 不重复生成卡片', () => {
  const response = createClarificationResponse(question, '销售额');
  let messages = [{ id: 'q1', role: 'tool', artifact: { human_input: question } }, { id: 'u2', role: 'user', text: '销售额', pending: true, additionalKwargs: { human_input_response: response } }];
  assert.equal(clarificationState(messages).answers.get(question.request_id).pending, true);
  messages = applyStreamEvent(messages, { event: 'values', data: { messages: [ { ...questionMessage, id: 'q1' }, { id: 'u2', type: 'human', content: '销售额', additional_kwargs: { human_input_response: response } } ] } }, 'r2');
  assert.equal(messages.length, 2);
  assert.equal(clarificationState(messages).answers.get(question.request_id).pending, false);
});

test('回答通过原有同一会话接口发送，模型能读文字，前端能关联问题', async t => {
  const response = createClarificationResponse(question, '订单数');
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    assert.equal(path, '/api/threads/original-thread/runs/stream');
    const message = JSON.parse(options.body).input.messages[0];
    assert.equal(message.content, '订单数');
    assert.equal(message.additional_kwargs.human_input_response.request_id, question.request_id);
    assert.equal(message.additional_kwargs.hide_from_ui, true);
    return responseFromParts([new TextEncoder().encode('event: end\ndata: null\n\n')]);
  });
  await streamRun('original-thread', { role: 'user', content: response.value, additional_kwargs: { hide_from_ui: true, human_input_response: response } }, () => {}, 'alice');
});

