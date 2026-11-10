# 前端使用说明

React + Vite。保留公司 SVG Logo 和原页面样式，已经接入后端真实聊天接口。
支持新建对话、流式回复、历史消息、工具结果、运行记录和运行详情。

## 用户入口

先输入 `user_id` 登录，再查看该用户的会话。当前是用户 ID 入口，没有密码验证。
登录用户保存在当前标签页的 sessionStorage，刷新后继续使用；退出后回到登录页。
切换用户会卸载旧聊天页面、断开旧流连接，重新加载新用户的数据。

会话列表从后端分页读取，支持按标题查找。新对话在第一次发送消息时登记到服务器，
默认标题取第一条消息的前 24 个字符；改名也保存到服务器。
发送消息（包括追问表单回答）、读取历史、加载更早消息和查询运行记录都会带上 `user_id`。

## 追问卡片

助手需要补充信息时，会显示问题卡片。可以选择一个选项，也可以填写自己的回答，
点击“提交并继续”后，仍在当前会话中继续处理。普通聊天输入框也可以直接回答。
回答会带上问题 ID，刷新后通过后端历史消息恢复“已回答”状态。
通过卡片提交的回答只在原卡片显示，不再新增用户气泡；在普通输入框发送的回答仍显示气泡。

支持单个问题、单选、自由文字和多字段表单。表单提交后保留控件及填写内容，设为不可编辑；
刷新后从回答消息恢复字段值，界面只显示易读摘要，不展示提交内容中的字段 JSON。
这沿用现有后端的做法：回答会交给模型继续判断，再决定是否调用工具。

## 接入的接口

| 接口 | 页面功能 |
| --- | --- |
| `GET /api/threads?user_id=...` | 加载当前用户的会话列表 |
| `POST /api/threads` | 第一次发送消息前登记会话和用户 |
| `GET /api/threads/{thread_id}` | 打开和同步会话时核对归属 |
| `PATCH /api/threads/{thread_id}` | 保存会话名称 |
| `POST /api/threads/{thread_id}/runs/stream` | 发送消息，逐步接收回复 |
| `GET /api/threads/{thread_id}/messages` | 读取历史消息，每页 50 条，可加载更早的消息 |
| `GET /api/threads/{thread_id}/runs` | 查看当前会话最近 50 次运行 |
| `GET /api/runs/{run_id}` | 查看状态、失败原因和时间 |

流结束后会查询运行详情，确认成功或失败，再读取已保存的消息。
刷新页面时，如果任务还在运行，会每隔 2 秒同步历史消息和运行状态。
网络断开不会自动重发问题，以免执行两次；可以点击“重新同步”确认结果。

点击“会话 ID”复制，再通过“通过 ID 打开会话”可打开属于当前用户的已有会话。
“移出本机列表”只按当前用户保存本机隐藏偏好，不删除服务器记录。
旧版本没有用户归属的浏览器会话列表不自动导入；消息和会话仍以服务器数据为准。
当前用户 ID 只是后端的归属标签与筛选条件，服务端尚未接入登录鉴权。

后端暂未提供取消、附件上传、重新生成、推荐问题等接口，页面不会模拟这些能力。

## 第一次安装

需要 Node.js 20.19+ 或 22.12+（建议使用当前 LTS 版本）。在项目根目录执行：

```bash
cd frontend
npm install
```

后续启动不需要重复安装。更换依赖后再执行 `npm install`。

## 方式一：开发时使用 5173

在项目根目录开两个终端：

```bash
# 终端一：后端
./scripts/start-backend.sh --reload
```

```bash
# 终端二：前端
./scripts/start-frontend.sh
```

打开 http://127.0.0.1:5173 。修改 React 代码后，页面自动更新。

前端接口地址统一写成 `/api/...`。开发时 Vite 把这些请求转到
`http://127.0.0.1:8000`，无需额外配置跨域。后端更换端口时，修改
`vite.config.js` 中的 `server.proxy['/api'].target`。

## 方式二：构建后通过后端访问

在项目根目录执行：

```bash
./scripts/build-frontend.sh
./scripts/start-backend.sh
```

打开 http://127.0.0.1:8000 。接口文档仍为 http://127.0.0.1:8000/docs 。
此时只需要后端服务，不需要启动 5173。

`build` 会自动生成 `backend/static/index.html` 和 `backend/static/assets/`，
不需要手动复制。该目录只放构建产物，每次构建会清空并重新生成。
修改前端后要重新构建；第一次构建前已启动后端的，需要重启后端。
部署时必须一起携带 `backend/static` 目录，它不进入版本管理。

开发和构建方式的浏览器地址不同，需要各自输入用户 ID；相同用户能读取同一后端的会话。

## 文件位置

| 文件 | 作用 |
| --- | --- |
| `src/App.jsx` | 聊天页面、消息渲染和会话管理 |
| `src/hooks/useChat.js` | 聊天请求、历史同步和运行状态 |
| `src/api/client.js` | 携带用户 ID 的会话、消息、运行接口及 SSE 读取 |
| `src/lib/messages.js` | 消息格式转换和增量合并 |
| `src/components/RunHistory.jsx` | 运行记录和详情弹窗 |
| `src/components/ClarificationCard.jsx` | 问题卡片、选择和填写回答 |
| `src/lib/humanInput.js` | 追问协议和历史回答状态 |
| `src/Icons.jsx` | 页面小图标 |
| `src/styles.css` | 页面样式 |
| `src/components/Login.jsx` | 用户 ID 登录入口 |
| `src/storage.js` | 当前用户、按用户区分的本机偏好，不保存消息内容 |
| `src/assets/company-logo.svg` | 公司原始 SVG Logo |
| `vite.config.js` | 开发端口、接口代理和构建位置 |

`index.html` 现在是 React 的入口，请通过上述服务访问，不再直接双击打开。

## 验证

```bash
cd frontend
npm test
npm run build
```

测试覆盖 SSE 分块、中文解码、消息去重、追问表单历史、用户 ID 传递、会话归属核对和接口错误处理。
如需连接其他端口，可运行 `REPORT_AGENT_BACKEND_URL=http://127.0.0.1:8001 npm run dev`。
