import { useCallback, useEffect, useRef, useState } from 'react';
import { createThread, getRun, getThread, listMessages, listRuns, listThreads, renameThread, streamRun } from '../api/client.js';
import { applyStreamEvent, historyMessages, isActive, mergeMessages } from '../lib/messages.js';
import { loadPreferences, newSession, savePreferences, uid } from '../storage.js';
import { clarificationState, createClarificationResponse } from '../lib/humanInput.js';

const sessionOf = thread => ({ id: thread.thread_id, title: thread.display_name || '新对话', draft: false });

export function useChat(userId, toast) {
  const [catalog, setCatalog] = useState(() => {
    const session = newSession();
    return { activeId: session.id, sessions: [session] };
  });
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState('');
  const preferences = useRef(loadPreferences(userId));
  const catalogVersion = useRef(0);
  const catalogReady = useRef(false);
  const [views, setViews] = useState({});
  const [streamingThread, setStreamingThread] = useState(null);
  const live = useRef(null);
  const requests = useRef({});
  const mounted = useRef(true);
  const updateView = useCallback((id, patch) => {
    if (!mounted.current) return;
    setViews(previous => ({ ...previous, [id]: typeof patch === 'function' ? patch(previous[id] || {}) : { ...previous[id], ...patch } }));
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; live.current?.controller.abort(); };
  }, []);
  useEffect(() => {
    if (!catalogReady.current) return;
    preferences.current.activeId = catalog.activeId;
    savePreferences(userId, preferences.current);
  }, [catalog.activeId, userId]);

  const refreshCatalog = useCallback(async (signal) => {
    const version = ++catalogVersion.current;
    setCatalogLoading(true); setCatalogError('');
    try {
      const rows = [];
      for (let offset = 0; ; offset += 50) {
        const page = await listThreads(userId, { offset, signal });
        rows.push(...page);
        if (page.length < 50) break;
      }
      if (!mounted.current || signal?.aborted || version !== catalogVersion.current) return;
      const firstLoad = !catalogReady.current;
      catalogReady.current = true;
      setCatalog(previous => {
        const sessions = rows.filter(row => row.user_id === userId && !preferences.current.hiddenIds.includes(row.thread_id)).map(sessionOf);
        const draft = previous.sessions.find(item => item.draft && item.id === previous.activeId);
        if (draft && (!firstLoad || !sessions.length)) sessions.unshift(draft);
        if (!sessions.length) sessions.push(newSession());
        const preferred = firstLoad ? preferences.current.activeId : previous.activeId;
        return { sessions, activeId: sessions.some(item => item.id === preferred) ? preferred : sessions[0].id };
      });
    } catch (error) {
      if (mounted.current && version === catalogVersion.current && error.name !== 'AbortError') setCatalogError(error.message);
    } finally {
      if (mounted.current && !signal?.aborted && version === catalogVersion.current) setCatalogLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshCatalog(controller.signal);
    return () => controller.abort();
  }, [refreshCatalog]);

  const refreshThread = useCallback(async (id, { silent = false, preserve = false, keepError = '' } = {}) => {
    const version = (requests.current[id] || 0) + 1;
    requests.current[id] = version;
    if (!silent) updateView(id, { loading: true, error: '' });
    try {
      const thread = await getThread(id, userId);
      const [rows, runs] = await Promise.all([listMessages(id, { userId }), listRuns(id, userId)]);
      let run = runs[0] || null;
      if (run) run = await getRun(run.run_id, userId);
      if (!mounted.current || requests.current[id] !== version) return null;
      const messages = historyMessages(rows);
      updateView(id, previous => ({
        ...previous, loading: false, loaded: true, runs, run,
        messages: preserve && !rows.length ? previous.messages || [] : messages,
        oldestSeq: rows[0]?.seq, hasMore: rows.length === 50,
        error: keepError,
      }));
      setCatalog(previous => ({ ...previous, sessions: previous.sessions.map(item => item.id === id ? {
        ...item, ...sessionOf(thread), runId: run?.run_id || null,
      } : item) }));
      return { run, rows };
    } catch (error) {
      if (mounted.current && requests.current[id] === version) updateView(id, { loading: false, loaded: false, error: keepError || error.message });
      return null;
    }
  }, [updateView, userId]);

  const activeId = catalog.activeId;
  const activeSession = catalog.sessions.find(item => item.id === activeId);
  const storedView = views[activeId] || {};
  const view = {
    ...storedView,
    loading: catalogLoading || storedView.loading,
    loaded: !catalogLoading && !catalogError && (activeSession?.draft || storedView.loaded),
    error: catalogError || storedView.error,
  };
  useEffect(() => {
    if (!catalogLoading && !catalogError && !activeSession?.draft && live.current?.threadId !== activeId) void refreshThread(activeId);
  }, [activeId, activeSession?.draft, catalogLoading, catalogError, refreshThread]);

  // 刷新页面后没有可重连的流接口；通过运行详情和历史消息同步进行中的任务。
  useEffect(() => {
    if (!isActive(view.run?.status) || streamingThread === activeId) return;
    const timer = setTimeout(() => void refreshThread(activeId, { silent: true }), 2000);
    return () => clearTimeout(timer);
  }, [activeId, view.run, view.error, streamingThread, refreshThread]);

  async function send(text, response = null) {
    text = text.trim();
    if (!text) return false;
    if (live.current || isActive(view.run?.status) || view.loading || !view.loaded) {
      toast('请等待当前任务完成后再发送。'); return false;
    }
    const id = activeId;
    const previousRun = view.run;
    const openRequest = clarificationState(view.messages || []).openRequest;
    const humanResponse = response || (openRequest ? createClarificationResponse(openRequest, text) : null);
    const controller = new AbortController();
    const request = { threadId: id, runId: null, controller };
    live.current = request;
    requests.current[id] = (requests.current[id] || 0) + 1; // 旧的历史请求不能覆盖流式内容。
    setStreamingThread(id);
    const additional_kwargs = humanResponse ? {
      human_input_response: humanResponse,
      // 卡片提交只在原卡片显示答案；普通输入框发送的消息仍显示气泡。
      ...(response ? { hide_from_ui: true } : {}),
    } : {};
    const userMessage = { id: uid(), role: 'user', content: text, additional_kwargs };
    const optimistic = { id: userMessage.id, role: 'user', text, additionalKwargs: additional_kwargs, pending: true };
    updateView(id, previous => ({ ...previous, messages: [...(previous.messages || []), optimistic], run: { status: 'pending' }, error: '', loaded: true }));
    try {
      const title = activeSession.title === '新对话' ? text.slice(0, 24) : activeSession.title;
      const thread = activeSession.draft
        ? await createThread(userId, { threadId: id, title }, controller.signal)
        : await getThread(id, userId, controller.signal);
      if (!activeSession.draft && thread.display_name !== title && activeSession.title === '新对话') {
        await renameThread(id, userId, title, controller.signal);
        thread.display_name = title;
      }
      if (!mounted.current) return false;
      setCatalog(previous => ({ ...previous, sessions: previous.sessions.map(item => item.id === id ? { ...item, ...sessionOf(thread) } : item) }));
      await streamRun(id, userMessage, entry => {
        if (!mounted.current) return;
        if (entry.event === 'metadata') {
          request.runId = entry.data.run_id;
          setCatalog(previous => ({ ...previous, sessions: previous.sessions.map(item => item.id === id ? { ...item, runId: request.runId } : item) }));
          updateView(id, { run: { run_id: request.runId, status: 'running' } });
        } else if (entry.event === 'messages' || entry.event === 'values') {
          updateView(id, previous => ({ ...previous, messages: applyStreamEvent(previous.messages || [], entry, request.runId || id) }));
        } else if (entry.event === 'error') {
          throw new Error(entry.data?.message || '生成失败，请查看任务记录。');
        }
      }, userId, controller.signal);
      // end 只表示流结束。用新增的详情接口确认 success / error，再读取落库后的消息。
      if (mounted.current) await refreshThread(id, { silent: true, preserve: true });
    } catch (error) {
      if (error.name !== 'AbortError' && mounted.current) {
        // 尚未拿到 run_id：请求没有确认成功，撤回本机的“已提交”，允许重试。
        if (!request.runId) updateView(id, previous => ({ ...previous, run: previousRun, messages: (previous.messages || []).filter(message => message.id !== userMessage.id) }));
        updateView(id, { error: error.message });
        if (!activeSession.draft || request.runId) await refreshThread(id, { silent: true, preserve: true, keepError: error.message });
      }
      return false;
    } finally {
      if (live.current === request) live.current = null;
      if (mounted.current) setStreamingThread(null);
    }
    return true;
  }

  async function loadOlder() {
    const id = activeId;
    if (!view.oldestSeq || view.paging || live.current?.threadId === id) return;
    updateView(id, { paging: true });
    try {
      const rows = await listMessages(id, { userId, before: view.oldestSeq });
      updateView(id, previous => ({ ...previous, messages: mergeMessages(historyMessages(rows), previous.messages || []), oldestSeq: rows[0]?.seq || previous.oldestSeq, hasMore: rows.length === 50, paging: false }));
    } catch (error) { updateView(id, { paging: false }); toast(error.message); }
  }

  function select(id) { setCatalog(previous => ({ ...previous, activeId: id })); }
  function create() {
    if (catalogLoading || catalogError) { toast('请先加载会话列表。'); return; }
    const empty = catalog.sessions.find(item => item.draft);
    if (empty) { select(empty.id); return; }
    const session = newSession();
    setCatalog(previous => ({ activeId: session.id, sessions: [session, ...previous.sessions] }));
  }
  async function open(id) {
    id = id.trim();
    if (!/^[\w-]{1,64}$/.test(id)) { toast('会话 ID 需为 1～64 个字母、数字、下划线或短横线。'); return false; }
    try {
      const thread = await getThread(id, userId);
      if (!mounted.current) return false;
      preferences.current.hiddenIds = preferences.current.hiddenIds.filter(item => item !== id);
      savePreferences(userId, preferences.current);
      const session = sessionOf(thread);
      setCatalog(previous => ({ activeId: id, sessions: previous.sessions.some(item => item.id === id) ? previous.sessions : [session, ...previous.sessions] }));
      return true;
    } catch (error) { if (mounted.current) toast(error.message); return false; }
  }
  async function rename(id, title) {
    try {
      const session = catalog.sessions.find(item => item.id === id);
      if (!session.draft) await renameThread(id, userId, title);
      if (!mounted.current) return false;
      setCatalog(previous => ({ ...previous, sessions: previous.sessions.map(item => item.id === id ? { ...item, title } : item) }));
      return true;
    } catch (error) { if (mounted.current) toast(error.message); return false; }
  }
  function remove(id) {
    if (live.current?.threadId === id || isActive(views[id]?.run?.status)) { toast('任务执行中，暂时不能移出会话。'); return false; }
    preferences.current.hiddenIds = [...new Set([...preferences.current.hiddenIds, id])];
    savePreferences(userId, preferences.current);
    setCatalog(previous => {
      let sessions = previous.sessions.filter(item => item.id !== id);
      if (!sessions.length) sessions = [newSession()];
      return { sessions, activeId: previous.activeId === id ? sessions[0].id : previous.activeId };
    });
    return true;
  }

  return { catalog, catalogLoading, catalogError, view, streamingThread, send, refresh: () => live.current?.threadId === activeId ? toast('正在接收回复，无需刷新。') : refreshCatalog(), loadOlder, select, create, open, rename, remove };
}
