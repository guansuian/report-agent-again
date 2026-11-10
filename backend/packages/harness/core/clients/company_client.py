"""公司接口客户端：使用当前请求传入的用户 Token，不登录、不缓存或刷新凭据。"""

from __future__ import annotations

from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx

from core.runtime.request_context import get_request_client, get_request_user


class CompanyClientError(RuntimeError):
    """接口失败；错误文本不包含 Token。"""

    def __init__(self, message: str, *, status_code: int | None = None, code: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class CompanyClient:
    """HTTP 连接可以共用，每次发送时单独读取本请求的 Token。"""

    def __init__(self, base_url: str, *, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        url = httpx.URL(base_url)
        if (url.scheme not in ('http', 'https') or not url.host
                or url.path not in ('', '/') or url.query or url.fragment or url.userinfo):
            raise ValueError('base_url 只能包含协议、主机和端口。')
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                 follow_redirects=False, headers={'Accept': 'application/json'},
                                 cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])))

    def request(self, method: str, path: str, *,
                params: dict[str, Any] | None = None, json: Any = None) -> dict[str, Any]:
        if not path.startswith('/') or path.startswith('//') or '\\' in path:
            raise ValueError('path 必须是以单个 / 开头的公司接口路径。')
        if httpx.URL(path).path == '/api/v1/auth/login':
            raise ValueError('Agent 不执行登录，请由公司后端提供用户 Token。')
        user = get_request_user()
        headers = {'Authorization': 'Bearer ' + user.token}
        if user.device_fingerprint is not None:
            headers['X-Device-Fingerprint'] = user.device_fingerprint
        try:
            # Token 只加在本次 HTTP 请求上，不修改共享客户端的默认请求头。
            # 不接受会话Cookie，避免共享连接时缓存其他用户的会话凭据。
            response = self._http.request(method, path, params=params, json=json,
                                          headers=headers)
        except httpx.TimeoutException:
            raise CompanyClientError('公司接口请求超时。') from None
        except httpx.HTTPError:
            raise CompanyClientError('公司接口网络请求失败。') from None
        if response.status_code == 401:
            raise CompanyClientError('用户认证已失效，请由调用方更新 Token 后重试。', status_code=401)
        if response.status_code == 403:
            raise CompanyClientError('当前用户没有访问该数据的权限。', status_code=403)
        if response.status_code != 200:
            raise CompanyClientError('公司接口 HTTP 状态不是 200。', status_code=response.status_code)
        try:
            result = response.json()
        except ValueError:
            raise CompanyClientError('公司接口未返回有效 JSON。') from None
        if not isinstance(result, dict):
            raise CompanyClientError('公司接口响应必须是 JSON 对象。')
        code = result.get('code')
        if type(code) is not int or code != 200:
            message = {401: '用户认证已失效，请由调用方更新 Token 后重试。',
                       403: '当前用户没有访问该数据的权限。',
                       20003: '用户认证已失效，请由调用方更新 Token 后重试。',
                       20004: '当前用户没有访问该数据的权限。',
                       20005: '公司接口拒绝了查询参数，请核对条件。',
                       20006: '公司接口返回业务错误20006，请联系后端核对数据。'}.get(
                           code if type(code) is int else None, '公司接口业务状态不是 200。')
            raise CompanyClientError(message, code=code,
                                     status_code={401: 401, 403: 403, 20003: 401, 20004: 403}.get(
                                         code if type(code) is int else None))
        return result

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request('GET', path, params=params)

    def post(self, path: str, *, json: Any = None) -> dict[str, Any]:
        return self.request('POST', path, json=json)

    def download_export(self, export_id: str, *, max_bytes: int = 50 * 1024 * 1024) -> tuple[bytes, str]:
        """鉴权读取导出文件流；限制大小，不把二进制当JSON解析，不跟随外部重定向。"""
        from re import fullmatch

        if not fullmatch(r'[A-Za-z0-9_-]{1,128}', export_id):
            raise ValueError('无效的导出任务编号。')
        user = get_request_user()
        headers = {'Authorization': 'Bearer ' + user.token,
                   'Accept': 'text/csv, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
        if user.device_fingerprint is not None:
            headers['X-Device-Fingerprint'] = user.device_fingerprint
        try:
            with self._http.stream('GET', f'/api/v1/agent/exports/{export_id}/download',
                                   params={'contract_version': 'agent-data-v1'}, headers=headers) as response:
                if response.status_code in (401, 403):
                    raise CompanyClientError('文件下载认证失败或无权访问。', status_code=response.status_code)
                if response.status_code != 200:
                    raise CompanyClientError('文件下载失败，可能已过期。', status_code=response.status_code)
                content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                if content_type not in ('text/csv', 'application/csv',
                                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                        'application/octet-stream', 'application/vnd.ms-excel'):
                    raise CompanyClientError('下载接口没有返回CSV或Excel文件，可能是业务错误。')
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > max_bytes:
                        raise CompanyClientError('文件超过下载大小限制，请缩小导出范围后重试。')
                    content.extend(chunk)
                if not content:
                    raise CompanyClientError('下载接口返回了空文件。')
                return bytes(content), content_type
        except httpx.HTTPError:
            raise CompanyClientError('文件下载网络请求失败。') from None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> CompanyClient:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def get_company_client() -> CompanyClient:
    """工具获取本次运行使用的客户端；连接由 gateway 的 lifespan 创建和关闭。"""
    return get_request_client()
