"""serialization.py 的测试：messages 模式的二元组怎么变成 JSON。"""

from langchain_core.messages import AIMessageChunk

from core.runtime.serialization import serialize


def test_messages_tuple_becomes_two_element_list():
    """(chunk, metadata) → [chunk_dict, metadata_dict]，顺序不能反。"""
    chunk = AIMessageChunk(content="本月")
    data = serialize((chunk, {"langgraph_node": "agent"}), mode="messages")

    assert isinstance(data, list) and len(data) == 2
    assert data[0]["content"] == "本月"        # 下标 0 = 内容
    assert data[1] == {"langgraph_node": "agent"}   # 下标 1 = 元数据


def test_messages_mode_tolerates_non_dict_metadata():
    """metadata 不是 dict 时给空字典，别把 SSE 帧搞坏。"""
    data = serialize((AIMessageChunk(content="x"), None), mode="messages")
    assert data[1] == {}
