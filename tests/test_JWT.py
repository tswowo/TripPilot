"""登录链路集成测试：需要 服务器 PostgreSQL 可用。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from trippilot.api.main import app


def test_auth_flow():
    username = f"test_{uuid4().hex[:8]}"
    password = "test-password-123"

    with TestClient(app) as client:
        registered = client.post(
            "/api/register", json={"username": username, "password": password}
        )
        assert registered.status_code == 200
        assert registered.json()["username"] == username

        duplicate = client.post(
            "/api/register", json={"username": username, "password": password}
        )
        assert duplicate.status_code == 400

        wrong_password = client.post(
            "/api/login", json={"username": username, "password": "wrong-password"}
        )
        assert wrong_password.status_code == 401

        login = client.post("/api/login", json={"username": username, "password": password})
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert login.json()["token_type"] == "bearer"

        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == username
        assert me.json()["id"] == registered.json()["id"]

        assert client.get("/api/me").status_code == 401
        assert client.get(
            "/api/me", headers={"Authorization": "Bearer invalid-token"}
        ).status_code == 401
        assert client.post("/api/chat", json={"message": "你好"}).status_code == 401
