"""授权子系统 —— 对应真实 deer-flow 的 deerflow/authz/。

对「模型、工具、技能」三层做授权。动作按 resource + action 划分：
    model:use  /  tool:execute  /  skill:activate
真实实现内置 RBAC provider，也支持自定义 provider；并有 fail-open / fail-closed 两种语义。
"""
