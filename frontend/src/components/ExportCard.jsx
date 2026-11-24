import { useEffect, useRef, useState } from 'react';
import { Alert, Avatar, Button, Card, Collapse, ConfigProvider, Descriptions, Steps, Tag } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { downloadExport, getExportStatus } from '../api/client.js';
import { Icon } from '../Icons.jsx';
import { formatTime } from '../lib/messages.js';

const STATUS_INFO = {
  queued: { label: '排队中', color: 'orange', step: 0, description: '任务已提交，正在等待生成。你可以继续聊天。' },
  processing: { label: '生成中', color: 'processing', step: 1, description: '文件就绪后即可下载，你可以继续聊天。' },
  ready: { label: '可下载', color: 'success', step: 2, description: '文件已生成，可以下载。' },
  failed: { label: '生成失败', color: 'error', description: '文件生成失败，当前没有可下载文件。' },
  expired: { label: '已过期', color: 'default', description: '文件已超过保留期限，需要重新导出。' },
};
const SOURCE_LABELS = {
  region_analysis: '区域分析', habitat_analysis: '生境分析', environment_distribution: '环境区间分布',
};

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ExportCard({ artifact, userId, active, onRetry }) {
  // 历史消息只保存创建时的快照；挂载后按 export_id 查询最新状态，不重新创建任务。
  const initialTask = artifact.data;
  const exportId = initialTask.export_id;
  const [task, setTask] = useState(initialTask);
  const [confirmed, setConfirmed] = useState(false);
  const [checking, setChecking] = useState(false);
  const [statusError, setStatusError] = useState('');
  const [downloadError, setDownloadError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const downloadController = useRef(null);

  useEffect(() => {
    if (!active) return;
    setConfirmed(false);
    setStatusError('');
    const controller = new AbortController();
    let timer;

    async function refresh() {
      setChecking(true);
      try {
        const latest = await getExportStatus(exportId, userId, controller.signal);
        if (controller.signal.aborted) return;
        setTask(latest);
        setConfirmed(true);
        setStatusError('');
        // 等本次请求结束后再安排下一次，避免慢请求重叠；结束状态不再轮询。
        if (latest.status === 'queued' || latest.status === 'processing') {
          timer = setTimeout(refresh, 2500);
        }
      } catch (error) {
        if (!controller.signal.aborted) setStatusError(error.message);
        // 查询失败就停下，由用户点击重新查询，不持续重试认证或网络错误。
      } finally {
        if (!controller.signal.aborted) setChecking(false);
      }
    }

    void refresh();
    return () => { clearTimeout(timer); controller.abort(); };
  }, [exportId, userId, active, refreshVersion]);

  useEffect(() => () => downloadController.current?.abort(), []);

  async function download() {
    if (downloading || task.status !== 'ready') return;
    const controller = new AbortController();
    downloadController.current = controller;
    setDownloading(true);
    setDownloadError('');
    try {
      const blob = await downloadExport(exportId, userId, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = task.file_name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (!controller.signal.aborted) setDownloadError(error.message);
    } finally {
      if (!controller.signal.aborted) setDownloading(false);
    }
  }

  // 首次确认前不展示历史快照中的任务状态，避免刷新时先闪现“排队中”。
  const status = confirmed ? STATUS_INFO[task.status] : {
    label: statusError ? '状态待确认' : '正在查询文件状态',
    color: 'default',
    description: statusError ? '暂时无法确认文件的最新状态，请重新查询。' : '正在获取文件最新状态，请稍候。',
  };
  const pending = task.status === 'queued' || task.status === 'processing';
  const metadata = [task.format === 'xlsx' ? 'Excel 文件' : 'CSV 文件'];
  if (confirmed && task.row_count != null) metadata.push(`${task.row_count.toLocaleString('zh-CN')} 行`);
  if (confirmed && task.byte_size != null) metadata.push(formatFileSize(task.byte_size));

  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#6f79db', borderRadius: 10 } }}>
    <Card className="export-file-card" size="small" aria-label={`导出文件：${task.file_name}`}>
      <div className="export-file-heading">
        <Avatar className="export-file-icon" shape="square" size={48}>{task.format.toUpperCase()}</Avatar>
        <div className="export-file-copy"><strong>{task.file_name}</strong><span>{metadata.join(' · ')}</span></div>
        <Tag className="export-file-status" color={status.color} role="status" aria-live="polite">{status.label}</Tag>
      </div>
      {status.step !== undefined && <Steps className="export-file-steps" type="dot" size="small" responsive={false} current={status.step} status={task.status === 'ready' ? 'finish' : 'process'} items={[{ title: '排队' }, { title: '生成文件' }, { title: '可下载' }]} />}
      <div className="export-file-bottom">
        <div className="export-file-description"><p>{status.description}</p>{confirmed && task.expires_at && <span>有效期至 {formatTime(task.expires_at)}</span>}</div>
        {confirmed && (pending ? <Button disabled>等待就绪</Button> : task.status === 'ready'
          ? <Button type="primary" icon={<Icon name="output" />} loading={downloading} disabled={checking || Boolean(statusError)} onClick={() => void download()}>下载 {task.format === 'xlsx' ? 'Excel' : 'CSV'}</Button>
          : <Button onClick={() => onRetry(initialTask)}>重新导出</Button>)}
      </div>
      {confirmed && task.status === 'failed' && task.error && <Alert className="export-file-alert" type="error" showIcon title="生成失败原因" description={task.error} />}
      {statusError && <Alert className="export-file-alert" type="warning" showIcon title="状态查询失败" description={statusError} action={<Button size="small" loading={checking} onClick={() => setRefreshVersion(value => value + 1)}>重新查询</Button>} />}
      {downloadError && <Alert className="export-file-alert" type="error" showIcon title="文件下载失败" description={downloadError} action={<Button size="small" loading={checking} onClick={() => { setDownloadError(''); setRefreshVersion(value => value + 1); }}>查询最新状态</Button>} />}
      <Collapse className="export-file-details" ghost size="small" items={[{
        key: 'file-info', label: '文件信息', children: <Descriptions size="small" column={1} items={[
          { key: 'source', label: '数据来源', children: SOURCE_LABELS[initialTask.source] },
          { key: 'id', label: '任务编号', children: exportId },
          { key: 'scope', label: '导出范围', children: '保留原查询条件和 Top N，不受报表分页限制。' },
        ]} />,
      }]} />
    </Card>
  </ConfigProvider>;
}
