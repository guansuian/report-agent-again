"""把报表的实际请求参数整理为中文展示条件；不修改原始 query。"""

from core.tools.common.species_names import SPECIES_BY_CODE

_LABELS = {
    "start_time": "开始时间", "end_time": "结束时间", "time_unit": "时间粒度",
    "province": "省级地区", "city": "城市", "district": "区县", "street": "街道",
    "dev_ids": "设备 ID", "top_n": "前 N 名",
}
_TIME_UNITS = {"hour": "小时合计", "day": "按日", "week": "按 ISO 周", "month": "按月", "year": "按年"}
_SEX_NAMES = {0: "性别未知", 1: "雄性", 2: "雌性"}


def build_report_conditions(query: dict) -> list[dict[str, str]]:
    """供现有三个报表工具共用，返回 label/value 列表。

    query 来自工具已经校验、转换完成的接口参数；蚊种原名和性别编码
    只在展示值中翻译，原始参数继续保留用于记录实际查询条件。
    """
    species_name = "全部类别"
    if "monitor_type" in query:
        codes = query["monitor_type"].split(",")
        species_name = "、".join(SPECIES_BY_CODE[code] for code in codes)
    sex_name = _SEX_NAMES[query["sex"]] if "sex" in query else "全部性别"
    conditions = [
        {"label": "蚊种", "value": species_name},
        {"label": "性别", "value": sex_name},
    ]

    for key, value in query.items():
        if key in ("monitor_type", "sex"):
            continue
        if key == "time_unit":
            value = _TIME_UNITS[value]
        conditions.append({"label": _LABELS[key], "value": str(value)})

    if not any(key in query for key in ("province", "city", "district", "street")):
        conditions.append({"label": "地区范围", "value": "当前账号可见范围"})
    return conditions
