"""外部服务客户端：工具只处理业务，HTTP 请求在这一层统一处理。"""

from core.clients.company_client import CompanyClient, CompanyClientError, get_company_client

__all__ = ["CompanyClient", "CompanyClientError", "get_company_client"]
