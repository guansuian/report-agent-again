"""run_models.py 的测试：请求体格式校验。"""

import pytest
from pydantic import ValidationError

from app.gateway.run_models import RunCreateRequest


def test_normal_request():
    body = RunCreateRequest(input={"messages": []})
    assert body.input == {"messages": []}
    assert body.config is None


def test_stream_mode_accepts_str_and_list():
    assert RunCreateRequest(stream_mode="values").stream_mode == "values"
    assert RunCreateRequest(stream_mode=["values", "messages-tuple"]).stream_mode == [
        "values",
        "messages-tuple",
    ]


def test_config_sections_must_be_objects():
    # configurable 是 dict：合法
    RunCreateRequest(config={"configurable": {"model_name": "deepseek-chat"}})

    # configurable 是字符串：挡掉
    with pytest.raises(ValidationError):
        RunCreateRequest(config={"configurable": "not-a-dict"})

    # context 不是 dict：挡掉
    with pytest.raises(ValidationError):
        RunCreateRequest(config={"context": 123})


def test_unknown_field_rejected():
    # extra="forbid"：不认识的字段直接报错，防止前端拼错字段名被静默忽略
    with pytest.raises(ValidationError):
        RunCreateRequest(unknown_field=1)


def test_known_stream_modes_accepted():
    RunCreateRequest(stream_mode="values")
    RunCreateRequest(stream_mode="messages-tuple")
    RunCreateRequest(stream_mode=["values", "messages-tuple"])


def test_unknown_stream_mode_rejected():
    # 拼错的名字在边界上就挡掉，不要等到后台任务里才炸
    with pytest.raises(ValidationError):
        RunCreateRequest(stream_mode="message")   # 少了 -tuple
    with pytest.raises(ValidationError):
        RunCreateRequest(stream_mode=["values", "updates"])   # 暂不支持


def test_user_id_is_trimmed_and_blank_rejected():
    assert RunCreateRequest(user_id="  u1  ").user_id == "u1"
    assert RunCreateRequest().user_id is None

    with pytest.raises(ValidationError):
        RunCreateRequest(user_id="   ")   # 空白字符串没有意义，挡掉
    with pytest.raises(ValidationError):
        RunCreateRequest(user_id="u" * 65)   # 超过 64 字符
