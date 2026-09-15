from __future__ import annotations

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse


def create_demo_app() -> FastAPI:
    app = FastAPI(title="HoloQA Demo Application")
    users: list[str] = []

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/demo", response_class=HTMLResponse)
    def demo() -> str:
        rows = "".join(f"<li>{email}</li>" for email in users)
        return f"""<!doctype html><html><body><h1>User Management</h1>
<form method='post' action='/demo/users'><label for='email'>Email</label>
<input id='email' name='email' type='email' required><button type='submit'>Create user</button></form>
<p id='message'>Users</p><ul>{rows}</ul></body></html>"""

    @app.post("/demo/users", response_class=HTMLResponse)
    def create_user(email: str = Form(...)) -> str:
        if email not in users:
            users.append(email)
        return demo()

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_demo_app(), host="0.0.0.0", port=8765)
