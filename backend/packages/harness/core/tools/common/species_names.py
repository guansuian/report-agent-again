"""蚊种字典：中文闭集 + 中文→拉丁名映射。

为什么用中文 Literal 而不是拉丁名：
    - 用户日常说的是中文（「白纹伊蚊」），模型拿中文当参数更自然；
    - 拉丁名带空格 / 命名人（如 "Anopheles sinensis Wiedemann"），模型容易拼错，
      用闭集把非法值挡在 schema 层；
    - 拉丁名只在 SPECIES_CODES 里做内部映射，工具回传给模型的数据里
      也只出现中文，避免模型下一轮把拉丁名再当参数传回来。

注意：项目要求 Python >=3.10，不能用 Literal[*SPECIES_CODES] 或
    Literal[tuple] 这类 3.11+ 的写法，所以 SpeciesName 显式写死这 8 个
    中文名，并靠测试保证它和 SPECIES_CODES 的 key 完全一致（见 test_tool_params.py）。
"""

from __future__ import annotations

from typing import Literal

SpeciesName = Literal[
    "白纹伊蚊",
    "埃及伊蚊",
    "致倦库蚊",
    "淡色库蚊",
    "三带喙库蚊",
    "中华按蚊",
    "骚扰阿蚊",
    "摇蚊科",
]

SPECIES_CODES: dict[str, str] = {
    "白纹伊蚊": "Aedes albopictus",
    "埃及伊蚊": "Aedes aegypti",
    "致倦库蚊": "Culex quinquefasciatus",
    "淡色库蚊": "Culex pipiens pallens",
    "三带喙库蚊": "Culex tritaeniorhynchus Giles",
    "中华按蚊": "Anopheles sinensis Wiedemann",
    "骚扰阿蚊": "Armigeres subalbatus",
    "摇蚊科": "Chironomidae",
}

# 反向映射：公司接口返回拉丁名，工具要翻译回中文展示给用户 / 模型。
SPECIES_BY_CODE: dict[str, str] = {code: name for name, code in SPECIES_CODES.items()}
