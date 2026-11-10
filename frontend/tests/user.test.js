import test from 'node:test';
import assert from 'node:assert/strict';
import { createThread, getThread, listMessages, listThreads, renameThread, streamRun } from '../src/api/client.js';
import { loadPreferences, loadUserId, normalizeUserId, savePreferences, saveUserId } from '../src/storage.js';

test('空用户 ID 不会发起无用户筛选的请求', async t => {
  let called = false;
  t.mock.method(globalThis, 'fetch', async () => { called = true; return Response.json([]); });
  assert.throws(() => listThreads(' '), /用户 ID/);
  assert.throws(() => listMessages('t1'), /用户 ID/);
  await assert.rejects(streamRun('t1', { content: '你好' }, () => {}, ''), /用户 ID/);
  assert.equal(called, false);
});

test('用户 ID 去除空格并编码，列表支持翻页，创建会话写入同一个用户', async t => {
  const calls = [];
  const userId = '张三&研发';
  t.mock.method(globalThis, 'fetch', async (path, options = {}) => {
    calls.push({ path, options });
    return Response.json(options.method === 'POST' ? { thread_id: 't1', user_id: userId } : []);
  });
  await listThreads(` ${userId} `, { offset: 50 });
  await createThread(userId, { threadId: 't1', title: '销售分析' });
  const url = new URL(calls[0].path, 'http://localhost');
  assert.equal(url.searchParams.get('user_id'), userId);
  assert.equal(url.searchParams.get('offset'), '50');
  assert.deepEqual(JSON.parse(calls[1].options.body), { thread_id: 't1', user_id: userId, display_name: '销售分析' });
});

test('打开和改名时发现会话属于其他用户就停止，不发送 PATCH', async t => {
  const methods = [];
  t.mock.method(globalThis, 'fetch', async (_path, options = {}) => {
    methods.push(options.method || 'GET');
    return Response.json({ thread_id: 'other', user_id: 'bob' });
  });
  await assert.rejects(getThread('other', 'alice'), /当前用户/);
  await assert.rejects(renameThread('other', 'alice', '新名称'), /当前用户/);
  assert.deepEqual(methods, ['GET', 'GET']);
});

test('改名保存到服务器，HTTP 错误不会当成保存成功', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (path, options = {}) => {
    calls.push({ path, options });
    return options.method === 'PATCH'
      ? Response.json({ detail: '保存失败' }, { status: 503 })
      : Response.json({ thread_id: 't1', user_id: 'alice' });
  });
  await assert.rejects(renameThread('t1', 'alice', '销售分析'), /保存失败/);
  assert.equal(new URL(calls[1].path, 'http://localhost').searchParams.get('user_id'), 'alice');
  assert.deepEqual(JSON.parse(calls[1].options.body), { display_name: '销售分析' });
});

test('刷新保留用户，退出清空身份；本机偏好按用户分开且不读取旧会话列表', t => {
  const data = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  for (const name of ['sessionStorage', 'localStorage']) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value: storage, configurable: true });
    t.after(() => descriptor ? Object.defineProperty(globalThis, name, descriptor) : delete globalThis[name]);
  }
  data.set('report-agent-threads-v1', JSON.stringify({ sessions: [{ id: 'old' }] }));
  assert.equal(loadUserId(), null);
  saveUserId(' alice ');
  assert.equal(loadUserId(), 'alice');
  savePreferences('alice', { activeId: 'a1', hiddenIds: ['a2'] });
  assert.deepEqual(loadPreferences('alice'), { activeId: 'a1', hiddenIds: ['a2'] });
  assert.deepEqual(loadPreferences('bob'), { activeId: null, hiddenIds: [] });
  saveUserId(null);
  assert.equal(loadUserId(), null);
  assert.throws(() => normalizeUserId('x'.repeat(65)), /64/);
});
