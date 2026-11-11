// 当前标签页记住登录用户；会话和标题从后端读取，不沿用旧的无用户列表。
const USER_KEY = 'report-agent-user-v1';
const LOGIN_KEY = 'report-agent-login-v1';
// sessionStorage 不可用时，仍可在当前页面使用已填写的身份。
let currentLogin = null;
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

function normalizeLogin(login) {
  const userId = normalizeUserId(login.userId);
  const token = typeof login.token === 'string'
    ? login.token.trim().replace(/^Bearer(?:\s+|$)/i, '') : '';
  const deviceFingerprint = typeof login.deviceFingerprint === 'string'
    ? login.deviceFingerprint.trim() : '';

  if (!token || /\s/.test(token)) {
    throw new Error('请输入有效的 Token，内容不能包含空格或换行。');
  }
  if (/[\r\n]/.test(deviceFingerprint)) {
    throw new Error('设备指纹不能包含换行。');
  }
  return { userId, token, deviceFingerprint };
}

export function loadLogin() {
  if (currentLogin) return currentLogin;
  try {
    const saved = JSON.parse(sessionStorage.getItem(LOGIN_KEY));
    if (saved) currentLogin = normalizeLogin(saved);
  } catch { /* 旧版只有用户 ID 或登录信息失效时，重新填写。 */ }
  return currentLogin;
}

export function saveLogin(login) {
  // 先校验再保存；Token 不进入 localStorage、聊天消息或请求体。
  currentLogin = login ? normalizeLogin(login) : null;
  try {
    if (currentLogin) sessionStorage.setItem(LOGIN_KEY, JSON.stringify(currentLogin));
    else sessionStorage.removeItem(LOGIN_KEY);
  } catch { /* 存储不可用时，刷新页面需要重新填写。 */ }
  saveUserId(currentLogin?.userId || null);
  return currentLogin;
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
