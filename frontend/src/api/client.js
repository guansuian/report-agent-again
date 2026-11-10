import { normalizeUserId } from '../storage.js';

// 所有地址都用 /api：开发时由 Vite 代理，构建后直接请求同源后端。
async function checked(response) {
  if (response.ok) return response;
  let detail;
  try { detail = (await response.json()).detail; } catch { /* 非 JSON 错误页面 */ }
  if (Array.isArray(detail)) detail = detail.map(item => item.msg).join('；');
  const error = new Error(typeof detail === 'string' ? detail : `请求失败（${response.status}）`);
  error.status = response.status;
  throw error;
}

export async function getJSON(path, signal) {
  return requestJSON(path, { signal, cache: 'no-store' });
}

async function requestJSON(path, options) {
  try {
    const response = await checked(await fetch(path, options));
    return response.status === 204 ? null : await response.json();
  } catch (error) {
    if (error instanceof TypeError) throw new Error('无法连接服务，请确认后端已启动，然后重试。');
    throw error;
  }
}

const query = (userId, parameters = {}) => new URLSearchParams({ user_id: normalizeUserId(userId), ...parameters }).toString();
export const listThreads = (userId, { offset = 0, signal } = {}) => getJSON(
  `/api/threads?${query(userId, { limit: 50, offset })}`, signal,
);
export const listMessages = (threadId, { userId, before, signal } = {}) => getJSON(
  `/api/threads/${encodeURIComponent(threadId)}/messages?${query(userId, { limit: 50, ...(before ? { before_seq: before } : {}) })}`, signal,
);
export const listRuns = (threadId, userId, signal) => getJSON(`/api/threads/${encodeURIComponent(threadId)}/runs?${query(userId, { limit: 50 })}`, signal);
export const getRun = (runId, userId, signal) => getJSON(`/api/runs/${encodeURIComponent(runId)}?${query(userId)}`, signal);

export async function getThread(threadId, userId, signal) {
  userId = normalizeUserId(userId);
  const thread = await getJSON(`/api/threads/${encodeURIComponent(threadId)}?${query(userId)}`, signal);
  // 当前详情接口没有归属过滤；页面在展示、打开和改名前核对归属。
  if (thread.user_id !== userId) {
    const error = new Error('找不到属于当前用户的会话。');
    error.status = 404;
    throw error;
  }
  return thread;
}

export async function createThread(userId, { threadId, title }, signal) {
  userId = normalizeUserId(userId);
  const thread = await requestJSON('/api/threads', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ thread_id: threadId, user_id: userId, display_name: title }),
  });
  if (thread.user_id !== userId) throw new Error('会话归属不一致，请新建对话。');
  return thread;
}

export async function renameThread(threadId, userId, title, signal) {
  await getThread(threadId, userId, signal);
  return requestJSON(`/api/threads/${encodeURIComponent(threadId)}?${query(userId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ display_name: title }),
  });
}

// 网络分块不等于 SSE 事件：一条 JSON 可能拆在多次 read() 中。
export async function readSSE(response, onEvent) {
  if (!response.body) throw new Error('服务没有返回消息流。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', event = 'message', data = [], id, ended = false;
  function line(value) {
    value = value.replace(/\r$/, '');
    if (value === '') {
      if (data.length) {
        const payload = data.join('\n');
        onEvent({ event, id, data: JSON.parse(payload) });
        if (event === 'end') ended = true;
      }
      event = 'message'; data = [];
    } else if (!value.startsWith(':')) {
      const colon = value.indexOf(':');
      const field = colon < 0 ? value : value.slice(0, colon);
      const content = colon < 0 ? '' : value.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') event = content;
      if (field === 'data') data.push(content);
      if (field === 'id') id = content;
    }
  }
  try {
    while (!ended) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
        if (ended) break;
      }
      if (done) break;
    }
    if (!ended) throw new Error('连接已断开。任务可能仍在执行，请刷新记录确认结果。');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function streamRun(threadId, message, onEvent, userId, signal) {
  userId = normalizeUserId(userId);
  let response;
  try {
    response = await fetch(`/api/threads/${encodeURIComponent(threadId)}/runs/stream`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ user_id: userId, input: { messages: [message] }, stream_mode: ['values', 'messages-tuple'] }),
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('无法连接服务，请确认后端已启动，然后重试。');
  }
  await checked(response);
  await readSSE(response, onEvent);
}
