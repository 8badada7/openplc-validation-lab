"""用于本地系统测试的最小 OpenPLC Runtime API 客户端。"""

import time
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning


class OpenPLCAPIError(RuntimeError):
    """OpenPLC Runtime API 请求或响应不符合预期。"""


class OpenPLCClient:
    def __init__(
        self,
        base_url: str = "https://127.0.0.1:8443",
        timeout: float = 3.0,
        verify_tls: bool | str = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            # TLS verification 用于确认服务端证书由可信 CA 签发。本地 Runtime
            # 使用自签名证书时仅对此客户端关闭验证，不修改全局 SSL 设置。
            with warnings.catch_warnings():
                if self.verify_tls is False:
                    warnings.simplefilter("ignore", InsecureRequestWarning)
                response = self.session.request(
                    method,
                    url,
                    timeout=self.timeout,
                    verify=self.verify_tls,
                    **kwargs,
                )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            raise OpenPLCAPIError(f"OpenPLC API 请求失败：{method} {path}: {error}") from error

    @staticmethod
    def _status_value(response: requests.Response, operation: str) -> str:
        try:
            payload = response.json()
        except ValueError as error:
            raise OpenPLCAPIError(f"{operation} 返回的不是有效 JSON") from error

        status = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(status, str):
            raise OpenPLCAPIError(f"{operation} 响应缺少字符串 status：{payload!r}")
        return status

    def login(self, username: str, password: str) -> str:
        response = self._request(
            "POST",
            "/api/login",
            json={"username": username, "password": password},
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise OpenPLCAPIError("Login 返回的不是有效 JSON") from error

        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise OpenPLCAPIError("Login 响应缺少 access_token")

        self.session.headers.update({"Authorization": f"Bearer {token}"})
        return token

    def get_status(self) -> str:
        response = self._request("GET", "/api/status")
        return self._status_value(response, "Get status")

    def start_plc(self) -> str:
        response = self._request("GET", "/api/start-plc")
        status = self._status_value(response, "Start PLC")
        if status != "START:OK":
            raise OpenPLCAPIError(f"Start PLC 未成功：{status}")
        return status

    def stop_plc(self) -> str:
        response = self._request("GET", "/api/stop-plc")
        status = self._status_value(response, "Stop PLC")
        if status != "STOP:OK":
            raise OpenPLCAPIError(f"Stop PLC 未成功：{status}")
        return status

    def wait_for_status(
        self,
        expected: str,
        timeout: float = 5.0,
        poll_interval: float = 0.2,
    ) -> str:
        deadline = time.monotonic() + timeout
        last_status = None

        while True:
            last_status = self.get_status()
            if last_status == expected:
                return last_status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"等待 PLC 状态 {expected} 超时；最后状态为 {last_status}"
                )
            time.sleep(poll_interval)

    def close(self) -> None:
        self.session.close()
