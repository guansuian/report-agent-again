"""验证构建后的页面可访问，并且不会挡住后端接口。"""

from fastapi.testclient import TestClient

from app.gateway.app import create_app


def test_frontend_files_and_api_share_backend(tmp_path):
    (tmp_path / "index.html").write_text('<div id="root">React page</div>')
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text('console.log("frontend")')

    with TestClient(create_app(static_dir=tmp_path)) as client:
        assert "React page" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/api/threads/empty/messages").json() == []
        assert client.get("/api/missing").status_code == 404
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/src/App.jsx").status_code == 404


def test_backend_can_start_without_frontend_build(tmp_path):
    with TestClient(create_app(static_dir=tmp_path)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/threads/empty/messages").json() == []
