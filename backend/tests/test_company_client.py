"""公司接口的协议与凭据隔离测试；所有 HTTP 请求都由 MockTransport 接管。"""

import json

import httpx
import pytest

from core.clients import CompanyClient, CompanyClientError, get_company_client
from core.config.company_config import CompanyConfig
from core.runtime.request_context import RequestUser, get_request_user, use_company_access


def test_get_post_and_per_request_credentials():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 200, "data": {"count": 12}},
                              headers={"Set-Cookie": "session=other-user; Path=/"})

    with CompanyClient("https://company.test", transport=httpx.MockTransport(handle)) as client:
        with use_company_access(client, RequestUser("u1", "test-token-a", "device-a")):
            assert get_company_client() is client
            assert client.get("/api/count", params={"page": 2})["data"]["count"] == 12
        with use_company_access(client, RequestUser("u2", "test-token-b")):
            client.post("/api/count", json={"region": "深圳"})

    assert requests[0].url.params["page"] == "2"
    assert requests[0].headers["Authorization"] == "Bearer test-token-a"
    assert requests[0].headers["X-Device-Fingerprint"] == "device-a"
    assert requests[1].headers["Authorization"] == "Bearer test-token-b"
    assert "X-Device-Fingerprint" not in requests[1].headers
    assert "Cookie" not in requests[1].headers
    assert json.loads(requests[1].content) == {"region": "深圳"}
    with pytest.raises(ValueError, match="Token"):
        get_request_user()
    with pytest.raises(RuntimeError, match="上下文"):
        get_company_client()


@pytest.mark.parametrize("response, status, code", [
    (httpx.Response(401), 401, None),
    (httpx.Response(403), 403, None),
    (httpx.Response(500), 500, None),
    (httpx.Response(302, headers={"Location": "https://other.test"}), 302, None),
    (httpx.Response(200, text="invalid json"), None, None),
    (httpx.Response(200, json=[]), None, None),
    (httpx.Response(200, json={"code": 20003}), 401, 20003),
    (httpx.Response(200, json={"code": 20004}), 403, 20004),
    (httpx.Response(200, json={"code": 20005}), None, 20005),
])
def test_http_and_business_errors_are_not_success(response, status, code):
    with CompanyClient("https://company.test", transport=httpx.MockTransport(lambda _: response)) as client:
        with use_company_access(client, RequestUser("u1", "test-secret")):
            with pytest.raises(CompanyClientError) as error:
                client.get("/api/count")
    assert error.value.status_code == status
    assert error.value.code == code
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_do_not_include_credentials(error_type):
    def handle(request):
        raise error_type("diagnostic test-secret", request=request)

    with CompanyClient("https://company.test", transport=httpx.MockTransport(handle)) as client:
        with use_company_access(client, RequestUser("u1", "test-secret")):
            with pytest.raises(CompanyClientError) as error:
                client.get("/api/count")
    assert "test-secret" not in str(error.value)


def test_missing_token_never_sends_request():
    def handle(request):
        pytest.fail("缺少 Token 时不应访问网络")

    with CompanyClient("https://company.test", transport=httpx.MockTransport(handle)) as client:
        with use_company_access(client, None):
            with pytest.raises(ValueError, match="Token"):
                client.get("/api/count")


def test_nested_context_restores_identity_after_failure():
    with CompanyClient("https://company.test", transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        outer = RequestUser("u1", "test-token-a")
        with use_company_access(client, outer):
            with pytest.raises(RuntimeError, match="业务失败"):
                with use_company_access(client, RequestUser("u2", "test-token-b")):
                    raise RuntimeError("业务失败")
            assert get_request_user() is outer
        with pytest.raises(ValueError, match="Token"):
            get_request_user()


@pytest.mark.parametrize("path", ["https://other.test/api", "//other.test/api", "/api/v1/auth/login"])
def test_rejects_external_paths_and_login(path):
    with CompanyClient("https://company.test", transport=httpx.MockTransport(lambda _: pytest.fail())) as client:
        with pytest.raises(ValueError):
            client.get(path)


def test_download_export_and_size_limit():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b"name,count\nmosquito,12\n",
                              headers={"Content-Type": "text/csv; charset=utf-8"})

    with CompanyClient("https://company.test", transport=httpx.MockTransport(handle)) as client:
        with use_company_access(client, RequestUser("u1", "test-token", "device-a")):
            content, content_type = client.download_export("export-1")
            assert content.startswith(b"name,count") and content_type == "text/csv"
            with pytest.raises(CompanyClientError, match="大小限制"):
                client.download_export("export-1", max_bytes=3)
            with pytest.raises(ValueError):
                client.download_export("../other")
    assert requests[0].url.path == "/api/v1/agent/exports/export-1/download"
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].headers["X-Device-Fingerprint"] == "device-a"


@pytest.mark.parametrize("response", [
    httpx.Response(403),
    httpx.Response(200, json={"code": 20005}),
    httpx.Response(200, content=b"", headers={"Content-Type": "text/csv"}),
])
def test_invalid_download_is_not_returned_as_a_file(response):
    with CompanyClient("https://company.test", transport=httpx.MockTransport(lambda _: response)) as client:
        with use_company_access(client, RequestUser("u1", "test-token")):
            with pytest.raises(CompanyClientError):
                client.download_export("export-1")


def test_company_config_uses_environment_and_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("COMPANY_BASE_URL", "https://company.test")
    assert CompanyConfig().base_url == "https://company.test"
    assert CompanyConfig(base_url="https://override.test").base_url == "https://override.test"
