# 公司接口客户端

工具负责查询条件、业务计算和结果整理；`CompanyClient` 统一处理 HTTP、用户 Token 和接口错误。
目前只迁移客户端，没有迁移旧项目的业务工具或增加产物表。

## 怎么使用

同步业务工具中：

```python
from core.clients import get_company_client

client = get_company_client()
result = client.get("/api/v1/device/list", params={"page": 1, "page_size": 10})
# result 是完整接口响应，包括 code、data 等字段。
```

也支持 `client.post(path, json=...)`，以及
`content, content_type = client.download_export(export_id)`。
下载结果是文件字节和媒体类型，不应直接返回给模型；后续文件下载路由可使用它。
这是同步客户端；同步 `@tool` 可以直接使用，异步工具应通过 `asyncio.to_thread` 调用，避免阻塞事件循环。

## 地址怎么配置

默认保留旧项目的开发接口地址。可以在 harness 的 `.env` 中设置 `COMPANY_BASE_URL`，
或在 `config.yaml` 中明确配置（优先于环境变量默认值）：

```yaml
company:
  base_url: https://your-company-host.example
  timeout: 30
```

地址只包含协议、主机和端口，不包含接口路径。用户 Token 不写进配置。

## Token 从哪里来

调用现有接口 `POST /api/threads/{thread_id}/runs/stream`，请求头带：

```http
Authorization: Bearer <公司后端签发的用户Token>
X-Device-Fingerprint: <可选，按公司后端要求传>
```

请求体仍按现有格式传 `user_id` 和 `input`。`user_id` 仍只是归属标签，
不能代替 Token，也不代表网关已经实现了登录或会话归属校验。
当前前端只有 user_id 输入页，还没有收集或发送公司 Token。

传递过程：

```text
HTTP 请求头 → gateway → run_agent 的执行上下文 → 同步工具 → 公司接口
```

连接在服务器启动时创建、关闭时释放。每次请求单独带自己的 Token，
不会把它放进模型消息、RunnableConfig、State 或检查点，也不接受公司接口的会话 Cookie。
后台任务和 LangChain 同步工具线程可以继承当前执行上下文。

不传 Token 时，现有模拟工具仍能运行；实际访问公司接口会明确报缺少 Token。
客户端不登录、不刷新 Token、不自动重试，失败交给工具错误中间件处理。

在不经过网关的脚本中，可手动建立执行上下文：

```python
from core.clients import CompanyClient
from core.runtime.request_context import RequestUser, use_company_access

with CompanyClient("https://your-company-host.example") as client:
    with use_company_access(client, RequestUser(user_id="u1", token=user_token)):
        result = client.get("/api/v1/device/list")
```

`user_token` 由脚本调用方提供，退出上下文时自动清除本次绑定。
