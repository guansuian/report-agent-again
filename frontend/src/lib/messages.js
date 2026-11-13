export function textOf(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map(item => typeof item === 'string' ? item : item?.type === 'text' || item?.type === 'output_text' ? item.text || '' : '').join('');
  return '';
}

export function normalizeMessage(message, fallbackId, extra = {}) {
  const type = message.type || message.role;
  const role = type === 'human' || type === 'user' ? 'user' : type === 'tool' ? 'tool' : 'assistant';
  // 工具结果落库时可能还没有 message id，进入图状态后才由框架补上。
  // tool_call_id 在这两个阶段保持一致，用它避免旧结果被当成新消息追加。
  const id = role === 'tool' && message.tool_call_id
    ? `tool:${message.tool_call_id}` : message.id || fallbackId;
  return {
    id, role, text: textOf(message.content),
    name: message.name, toolCalls: message.tool_calls || [], pending: false, ...extra,
    artifact: message.artifact, additionalKwargs: message.additional_kwargs || {},
  };
}

export function historyMessages(rows) {
  return rows.map(row => {
    const role = row.event_type === 'llm.human.input' ? 'human' : row.event_type === 'llm.tool.result' ? 'tool' : 'ai';
    const message = typeof row.content === 'object' && row.content !== null ? row.content : { type: role, content: row.content };
    return normalizeMessage({ type: role, ...message }, `event-${row.seq}`, { seq: row.seq, runId: row.run_id });
  });
}

export function mergeMessages(previous, incoming) {
  const result = [...previous];
  for (const message of incoming) {
    const index = result.findIndex(item => item.id === message.id);
    if (index < 0) result.push(message); else result[index] = { ...result[index], ...message };
  }
  return result;
}

// 一轮回复可以包含多条 AI/工具消息；界面上合为一个回复区域。
// 只分组展示，不修改消息内容、ID 或后端 State。
export function groupMessagesForDisplay(messages) {
  const groups = [];
  let reply = null;
  for (const message of messages) {
    if (message.role === 'user') {
      reply = null; // 隐藏的用户回答也代表新一轮的开始。
      if (message.additionalKwargs?.hide_from_ui !== true) groups.push({ id: message.id, role: 'user', messages: [message] });
      continue;
    }
    if (message.additionalKwargs?.hide_from_ui === true) continue;
    if (!reply) {
      reply = { id: message.id, role: 'assistant', messages: [] };
      groups.push(reply);
    }
    reply.messages.push(message);
  }
  return groups;
}

// values 是完整快照，messages 是增量。相同 message id 始终更新同一个气泡。
export function applyStreamEvent(previous, entry, runId) {
  if (entry.event === 'values' && Array.isArray(entry.data?.messages)) {
    const messages = entry.data.messages
      .filter(message => !['system', 'remove'].includes(message.type || message.role))
      .map((message, index) => normalizeMessage(message, `${runId}-snapshot-${index}`));
    return mergeMessages(previous.filter(message => !message.transient), messages);
  }
  if (entry.event === 'messages' && Array.isArray(entry.data)) {
    const [chunk, metadata] = entry.data;
    if (!chunk || !['AIMessageChunk', 'ai', 'assistant'].includes(chunk.type || chunk.role)) return previous;
    const text = textOf(chunk.content);
    if (!text) return previous;
    const id = chunk.id || `${runId}-stream-${metadata?.langgraph_step ?? 'answer'}`;
    const existing = previous.find(message => message.id === id);
    return mergeMessages(previous, [{
      id, role: 'assistant', text: (existing?.text || '') + text,
      // 有 id 时可以与 values 对齐，无 id 的临时气泡在下次快照中清除。
      transient: !chunk.id,
    }]);
  }
  return previous;
}

export const STATUS_LABELS = { pending: '等待执行', running: '正在生成', success: '已完成', error: '执行失败', timeout: '执行超时', interrupted: '已中断' };
export const isActive = status => status === 'pending' || status === 'running';
export function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
}
