import { useEffect, useRef, useState } from 'react';
import { listRegions } from '../api/client.js';

// 放在聊天页使用：切换会话保留地区树，退出登录时随页面一起清除。
export function useRegions(userId) {
  const [tree, setTree] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(null);

  useEffect(() => {
    return () => request.current?.abort();
  }, []);

  async function load() {
    // null 表示尚未获取；空数组表示接口成功返回，但没有可见地区。
    if (tree !== null || request.current) return;

    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError('');
    try {
      const result = await listRegions(userId, controller.signal);
      if (!controller.signal.aborted) setTree(result.list);
    } catch (error) {
      if (!controller.signal.aborted) setError(error.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
      request.current = null;
    }
  }

  return { tree, loading, error, load };
}
