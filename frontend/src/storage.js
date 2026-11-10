// 当前标签页记住登录用户；会话和标题从后端读取，不沿用旧的无用户列表。
const USER_KEY = 'report-agent-user-v1';
const preferenceKey = userId => `report-agent-preferences-v1:${encodeURIComponent(userId)}`;
export const uid = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
export const newSession = (id = uid()) => ({ id, title: '新对话', runId: null, draft: true });

export function normalizeUserId(value) {
  const userId = typeof value === 'string' ? value.trim() : '';
  if (!userId || Array.from(userId).length > 64) throw new Error('请输入 1～64 个字符的用户 ID。');
  return userId;
}

export function loadUserId() {
  try { return normalizeUserId(sessionStorage.getItem(USER_KEY)); }
  catch { return null; }
}

export function saveUserId(userId) {
  try {
    if (userId) sessionStorage.setItem(USER_KEY, normalizeUserId(userId));
    else sessionStorage.removeItem(USER_KEY);
  } catch { /* 存储不可用时，仍允许在当前页面使用。 */ }
}

export function loadPreferences(userId) {
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey(userId)));
    return {
      activeId: typeof saved?.activeId === 'string' ? saved.activeId : null,
      hiddenIds: Array.isArray(saved?.hiddenIds) ? saved.hiddenIds.filter(id => typeof id === 'string') : [],
    };
  } catch { return { activeId: null, hiddenIds: [] }; }
}

export function savePreferences(userId, preferences) {
  try { localStorage.setItem(preferenceKey(userId), JSON.stringify(preferences)); }
  catch { /* 偏好不影响服务器里的会话和消息。 */ }
}
