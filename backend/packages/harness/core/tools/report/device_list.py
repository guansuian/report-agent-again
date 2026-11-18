"""查询当前设备明细和分页列表，只返回内容，不生成图表产物。"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field

from core.clients import get_company_client
from core.tools.common import (
    Page,
    RegionName,
    ToolDataError,
    ToolInputError,
    check_regions,
    get_region_tree,
    report_tool,
)

_DEVICE_TEXT_FIELDS = (
    "name", "code", "type", "province", "city", "district", "street",
    "monitor_address", "area_type",
)


def _normalize_device_rows(rows: list) -> list[dict]:
    """只提取回答需要的字段，保留原始 ID、状态码以及空值，不猜测设备信息。"""
    devices = []
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("设备列表中的每条记录必须是对象。")
        device_id = row.get("id")
        status = row.get("status")
        if type(device_id) is not int or device_id <= 0:
            raise ToolDataError("设备 id 必须是正整数，不能使用名称或编号代替。")
        if type(status) is not int:
            raise ToolDataError("设备 status 必须是整数状态码。")

        device = {"id": device_id, "status": status}
        for field in _DEVICE_TEXT_FIELDS:
            if field not in row:
                continue
            value = row[field]
            if value is not None and not isinstance(value, str):
                raise ToolDataError(f"设备字段 {field} 必须是文本或 null。")
            device[field] = value
        devices.append(device)
    return devices


@report_tool
def device_list(
    keyword: Annotated[str, Field(min_length=1)] | None = None,
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    tenant_id: Annotated[int, Field(gt=0, strict=True)] | None = None,
    page: Page = 1,
    page_size: Annotated[int, Field(ge=1, le=100, strict=True)] = 10,
) -> str:
    """按名称、编号关键词、地区或已知租户查询当前设备，返回明细列表和分页信息。

    不需要时间，不提供状态、设备类型、生境筛选，不生成图表或表格产物。
    单台问题只有唯一匹配时直接回答；多台匹配时展示候选并先确认，不能默认选第一台。
    设备列表问题直接展示本页，无需确认单台；下一页须保留原筛选条件和每页数量。
    查询监测数量时，将确认的 id 交给其他工具的 dev_ids，不能用 code 或名称代替。

    Args:
        keyword: 可选设备名称、编号或片段，支持模糊匹配，实际传给接口的 code 参数。
        province: 可选省级地区全称，如山西省。
        city: 可选城市全称，如太原市；通过地区树校验并补全上级。
        district: 可选区县全称；同名时需结合上级地区确定。
        street: 可选街道全称；同名时需结合上级地区确定。
        tenant_id: 可选已知且可访问的单个租户 ID，正整数，不编造，不支持多个租户。
        page: 页码，从 1 开始，默认 1；翻页时只修改页码，保留其他条件。
        page_size: 每页设备数量，默认 10，最多 100；本页列表不能称为全部匹配设备。
    """
    # 1. 关键词映射为 code；地区复用现有地区树校验，不额外创建客户端。
    if keyword is not None and not keyword.strip():
        raise ToolInputError("keyword 不能是空白文本，未指定关键词时请省略。")
    region_params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        region_params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    params = {
        "code": keyword, **region_params, "tenant_id": tenant_id,
        "page": page, "page_size": page_size,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 一次读取当前页；接口已包含状态、地址和生境，不追加设备详情请求。
    response = get_company_client().get("/api/v1/device/list", params=params)
    try:
        total = response["data"]["total"]
        rows = response["data"]["list"]
    except (KeyError, TypeError):
        raise ToolDataError("设备查询接口缺少 data.total 或 data.list。") from None
    if type(total) is not int or total < 0:
        raise ToolDataError("设备查询的 total 必须是非负整数。")
    if not isinstance(rows, list) or len(rows) > page_size or len(rows) > total:
        raise ToolDataError("设备查询的 list 必须是列表，条数不能超过 page_size 或 total。")
    devices = _normalize_device_rows(rows)

    # 3. 保留总匹配数和本页数量，避免将本页唯一一条误认为全范围唯一匹配。
    unique_match = total == 1 and len(devices) == 1
    if total == 0:
        message = "当前账号可见范围内未查到匹配设备，不能据此认定设备不存在或离线。"
    elif not devices:
        message = "当前页没有记录，请查询有效页码，不能据此认定没有匹配设备。"
    elif unique_match:
        message = "已找到唯一匹配设备。"
    else:
        message = "匹配多台设备；列表问题展示本页，单台设备问题请先确认目标。"
    result = {
        "message": message, "query": params,
        "total": total, "page": page, "page_size": page_size,
        "returned_count": len(devices), "has_more": page * page_size < total,
        "has_data": bool(devices), "unique_match": unique_match, "devices": devices,
        "note": "仅返回当前账号权限和筛选范围内的当前设备信息，不代表历史状态。"
                "status=1 表示在线，即正常运行；3 按接口文档表示删除；2、4 及其他状态保留原码，不推断含义。"
                "位置、生境等字段为空或未提供时不能编造。id 是设备内部 ID，code 是设备编号。"
                "total 是全部匹配数，devices 只是当前页；翻页保留所有筛选条件及 page_size。",
    }
    return json.dumps(result, ensure_ascii=False, allow_nan=False)
