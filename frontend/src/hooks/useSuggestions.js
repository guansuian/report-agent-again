import { useCallback, useEffect, useRef, useState } from 'react';
import { generateSuggestions, getSuggestionsConfig } from '../api/client.js';

const EMPTY_QUESTIONS = [];

export function useSuggestions({ userId, threadId, completedRunId, messages, ready }) {
  const [settings, setSettings] = useState({ config: null, error: '' });
  const [result, setResult] = useState({ key: null, questions: [], loading: false, error: '' });
  const handledRuns = useRef({});
  const latestMessages = useRef(messages);
  const pendingRequest = useRef(null);
  latestMessages.current = messages;
  const key = completedRunId ? `${threadId}:${completedRunId}` : null;

  useEffect(() => {
    const controller = new AbortController();
    async function loadConfig() {
      try {
        const config = await getSuggestionsConfig(userId, controller.signal);
        if (typeof config.enabled !== 'boolean' || !Number.isInteger(config.max_suggestions) || config.max_suggestions < 1) {
          throw new Error('推荐配置接口返回格式不正确。');
        }
        if (!controller.signal.aborted) setSettings({ config, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setSettings({ config: null, error: error.message });
      }
    }
    void loadConfig();
    return () => controller.abort();
  }, [userId]);

  useEffect(() => {
    setResult({ key, questions: [], loading: false, error: '' });
    if (!ready || !key || (!settings.config && !settings.error)) return;
    if (handledRuns.current[threadId] === completedRunId) return;
    handledRuns.current[threadId] = completedRunId;
    if (settings.error) {
      setResult({ key, questions: [], loading: false, error: settings.error });
      return;
    }
    if (!settings.config.enabled) return;

    // 只发送最近六条可见用户/AI 文字；不把工具数据、隐藏记忆或表单答案交给推荐接口。
    const recent = latestMessages.current
      .filter(message => ['user', 'assistant'].includes(message.role) && message.additionalKwargs?.hide_from_ui !== true && message.text?.trim())
      .slice(-6)
      .map(message => ({ role: message.role, content: message.text }));
    if (!recent.some(message => message.role === 'user')) return;

    const controller = new AbortController();
    pendingRequest.current = controller;
    setResult({ key, questions: [], loading: true, error: '' });
    async function loadQuestions() {
      try {
        const data = await generateSuggestions(threadId, {
          userId, messages: recent, n: settings.config.max_suggestions, signal: controller.signal,
        });
        if (!Array.isArray(data.suggestions) || data.suggestions.some(question => typeof question !== 'string' || !question.trim())) {
          throw new Error('推荐问题接口返回格式不正确。');
        }
        if (!controller.signal.aborted) setResult({ key, questions: data.suggestions, loading: false, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setResult({ key, questions: [], loading: false, error: error.message });
      }
    }
    void loadQuestions();
    return () => {
      controller.abort();
      if (pendingRequest.current === controller) pendingRequest.current = null;
    };
  }, [userId, threadId, completedRunId, key, ready, settings]);

  const dismiss = useCallback(() => {
    pendingRequest.current?.abort();
    setResult(previous => ({ ...previous, questions: [], loading: false, error: '' }));
  }, []);

  // 用会话和 run 双重匹配，切换后的第一帧也不会显示上一轮的结果。
  const current = ready && key && result.key === key ? result : null;
  return { questions: current?.questions || EMPTY_QUESTIONS, loading: Boolean(current?.loading), error: current?.error || '', dismiss };
}
