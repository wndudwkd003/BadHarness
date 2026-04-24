from __future__ import annotations

import json
from urllib.parse import urljoin, urlparse

import requests

from configs.config import C_SERVER_BASE_URL, LLM_TIMEOUT
from core.workspace import get_runtime_file


_SESSION: requests.Session | None = None


def _load_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        cookie_path = get_runtime_file("http_cookies.json")
        if cookie_path.exists():
            try:
                stored = json.loads(cookie_path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    _SESSION.cookies.update(stored)
            except Exception:
                pass
    return _SESSION


def _persist_session(session: requests.Session) -> None:
    cookie_path = get_runtime_file("http_cookies.json")
    cookie_path.write_text(
        json.dumps(session.cookies.get_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_url(path: str) -> str:
    base = C_SERVER_BASE_URL.rstrip("/") + "/"
    if path.startswith("http://") or path.startswith("https://"):
        parsed = urlparse(path)
        base_parsed = urlparse(base)
        if (parsed.scheme, parsed.netloc) != (base_parsed.scheme, base_parsed.netloc):
            raise RuntimeError("send_curl_request is restricted to the configured C server")
        return path
    return urljoin(base, path.lstrip("/"))


def send_curl_request(
    path: str = "/",
    method: str = "GET",
    data: dict | str | None = None,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
    use_session: bool = True,
    json_mode: bool = False,
) -> dict:
    """Send a restricted HTTP request to the configured C server."""
    url = _build_url(path)
    request_headers = dict(headers or {})
    payload = None
    json_payload = None
    form_payload = None

    if isinstance(data, dict):
        if json_mode:
            request_headers.setdefault("Content-Type", "application/json")
            json_payload = data
        else:
            form_payload = data
    elif isinstance(data, str):
        payload = data

    session = _load_session() if use_session else requests.Session()
    if cookies:
        session.cookies.update(cookies)

    response = session.request(
        method=method.upper(),
        url=url,
        data=payload,
        files=None,
        params=None,
        auth=None,
        hooks=None,
        stream=False,
        verify=True,
        cert=None,
        json=json_payload,
        headers=request_headers,
        cookies=None,
        allow_redirects=follow_redirects,
        timeout=LLM_TIMEOUT,
    ) if form_payload is None else session.request(
        method=method.upper(),
        url=url,
        data=form_payload,
        json=json_payload,
        headers=request_headers,
        allow_redirects=follow_redirects,
        timeout=LLM_TIMEOUT,
    )

    if use_session:
        _persist_session(session)

    return {
        "method": method.upper(),
        "url": response.url,
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": response.text[:4000],
        "cookies": session.cookies.get_dict(),
        "history": [
            {
                "status_code": item.status_code,
                "url": item.url,
            }
            for item in response.history
        ],
        "request_body": (
            json.dumps(json_payload, ensure_ascii=False)
            if json_payload is not None
            else json.dumps(form_payload, ensure_ascii=False)
            if form_payload is not None
            else payload
        ),
    }
