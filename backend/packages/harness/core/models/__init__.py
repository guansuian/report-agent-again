"""模型子系统 —— 对应真实 deer-flow 的 deerflow/models/。

负责把 config 里的模型档案转换成可用的聊天模型实例。
真实实现还包含各种 provider 适配（openai / deepseek / vllm / anthropic ...）
和推理能力契约（reasoning.py）。
"""
