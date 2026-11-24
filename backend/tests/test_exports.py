"""导出网关：模拟公司 HTTP 接口，不启动数据库或调用真实服务。"""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.exports import router
from core.clients import CompanyClient
from core.runtime.request_context import get_request_user

HEADERS = {"Authorization": "Bearer test-token", "X-Device-Fingerprint": "test-fingerprint"}
PATHS = ("/api/exports/exp_001", "/api/exports/exp_001/download")


@pytest.fixture
def export_api():
    requests = []
    settings = {
        "task": {
            "export_id": "exp_001", "status": "ready", "format": "csv",
            "file_name": "广东省区域分析.csv", "row_count": 10, "byte_size": 123,
            "expires_at": "2026-11-25T00:00:00+08:00", "error": None,
            "download_path": "https://untrusted.example/download", "applied_query": {},
        },
        "status_http": 200, "download_http": 200,
        "content_type": "text/csv", "content": b"city,count\nShenzhen,100\n",
    }

    def handle_request(request):
        requests.append(request)
        # 检查 HTTP 请求与本次执行上下文使用同一个用户，不只检查有无请求头。
        assert request.headers["Authorization"] == "Bearer " + get_request_user().token
        if request.url.path.endswith("/download"):
            return httpx.Response(settings["download_http"], content=settings["content"],
                                  headers={"Content-Type": settings["content_type"]})
        return httpx.Response(settings["status_http"], json={"code": 200, "data": settings["task"]})

    # 独立应用不挂项目 lifespan，因此不会连接数据库或初始化长期记忆。
    app = FastAPI()
    app.include_router(router)
    with CompanyClient("https://company.test", transport=httpx.MockTransport(handle_request)) as company:
        app.state.company_client = company
        with TestClient(app) as client:
            yield client, requests, settings


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("authorization", [None, "Basic invalid", "Bearer "])
def test_missing_or_invalid_token_is_rejected_before_company_request(export_api, path, authorization):
    client, requests, _ = export_api
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get(path, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert requests == []


@pytest.mark.parametrize("status", ["queued", "processing", "ready", "failed", "expired"])
def test_status_returns_latest_metadata_and_local_download_path(export_api, status):
    client, requests, settings = export_api
    settings["task"]["status"] = status
    settings["task"]["error"] = "生成失败" if status == "failed" else None
    response = client.get(PATHS[0], headers=HEADERS)
    assert response.status_code == 200
    task = response.json()
    assert task["export_id"] == "exp_001" and task["status"] == status
    assert task["row_count"] == 10 and task["byte_size"] == 123
    assert task["expires_at"] == settings["task"]["expires_at"]
    assert task["error"] == settings["task"]["error"]
    assert task["download_path"] == (PATHS[1] if status == "ready" else None)
    assert "applied_query" not in task
    assert response.headers["cache-control"] == "private, no-store"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET" and request.url.path == "/api/v1/agent/exports/exp_001"
    assert dict(request.url.params) == {"contract_version": "agent-data-v1"}
    assert request.headers["Authorization"] == HEADERS["Authorization"]
    assert request.headers["X-Device-Fingerprint"] == HEADERS["X-Device-Fingerprint"]


@pytest.mark.parametrize("status", ["queued", "processing", "failed", "expired"])
def test_unready_file_is_not_downloaded(export_api, status):
    client, requests, settings = export_api
    settings["task"]["status"] = status
    assert client.get(PATHS[1], headers=HEADERS).status_code == 409
    assert len(requests) == 1
    assert not requests[0].url.path.endswith("/download")


@pytest.mark.parametrize("file_format,content_type,content", [
    ("csv", "text/csv", b"city,count\nShenzhen,100\n"),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK-test-file"),
])
def test_ready_file_is_downloaded_with_same_identity_and_chinese_filename(
    export_api, file_format, content_type, content,
):
    client, requests, settings = export_api
    file_name = "广东省区域分析." + file_format
    settings["task"].update(format=file_format, file_name=file_name)
    settings.update(content_type=content_type, content=content)
    response = client.get(PATHS[1], headers=HEADERS)
    assert response.status_code == 200 and response.content == content
    assert response.headers["content-type"].split(";")[0] == content_type
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''" + quote(file_name, safe="")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert [request.url.path for request in requests] == [
        "/api/v1/agent/exports/exp_001", "/api/v1/agent/exports/exp_001/download",
    ]
    for request in requests:
        assert request.method == "GET"
        assert dict(request.url.params) == {"contract_version": "agent-data-v1"}
        assert request.headers["Authorization"] == HEADERS["Authorization"]
        assert request.headers["X-Device-Fingerprint"] == HEADERS["X-Device-Fingerprint"]


@pytest.mark.parametrize("field,value", [
    ("export_id", "exp_other"), ("status", "unknown"), ("file_name", "x\r\nheader"),
])
def test_invalid_or_wrong_task_does_not_trigger_download(export_api, field, value):
    client, requests, settings = export_api
    settings["task"][field] = value
    assert client.get(PATHS[1], headers=HEADERS).status_code == 502
    assert len(requests) == 1


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("upstream,expected", [(401, 401), (403, 403), (404, 404), (410, 410), (500, 502)])
def test_company_status_errors_are_reported(export_api, path, upstream, expected):
    client, requests, settings = export_api
    settings["status_http"] = upstream
    assert client.get(path, headers=HEADERS).status_code == expected
    assert len(requests) == 1


@pytest.mark.parametrize("upstream,expected", [(401, 401), (403, 403), (404, 404), (410, 410), (500, 502)])
def test_company_download_errors_are_reported(export_api, upstream, expected):
    client, requests, settings = export_api
    settings["download_http"] = upstream
    assert client.get(PATHS[1], headers=HEADERS).status_code == expected
    assert len(requests) == 2


@pytest.mark.parametrize("content_type,content", [("application/json", b'{"error":"failed"}'), ("text/csv", b"")])
def test_error_response_or_empty_file_is_not_delivered_as_download(export_api, content_type, content):
    client, _, settings = export_api
    settings.update(content_type=content_type, content=content)
    assert client.get(PATHS[1], headers=HEADERS).status_code == 502


@pytest.mark.parametrize("export_id", ["invalid:id", "x.y", "x" * 129])
def test_invalid_task_id_does_not_reach_company(export_api, export_id):
    client, requests, _ = export_api
    assert client.get(f"/api/exports/{export_id}", headers=HEADERS).status_code == 422
    assert requests == []


def test_concurrent_users_keep_their_own_credentials(export_api):
    client, requests, _ = export_api

    def query_for_user(user):
        headers = {"Authorization": f"Bearer {user}", "X-Device-Fingerprint": f"fp-{user}"}
        assert client.get(PATHS[0], headers=headers).status_code == 200

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(query_for_user, ("user_a", "user_b")))
    assert {request.headers["Authorization"] for request in requests} == {"Bearer user_a", "Bearer user_b"}
    for request in requests:
        user = request.headers["Authorization"].removeprefix("Bearer ")
        assert request.headers["X-Device-Fingerprint"] == f"fp-{user}"


def test_main_app_registers_both_export_routes():
    from app.gateway.app import create_app

    # 只检查路由表，不运行主应用的 lifespan。
    paths = create_app().openapi()["paths"]
    assert "get" in paths["/api/exports/{export_id}"]
    assert "get" in paths["/api/exports/{export_id}/download"]
