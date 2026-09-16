from __future__ import annotations

import os
import re
import html

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse


def create_demo_app() -> FastAPI:
    app = FastAPI(title="HoloQA Demo Application")
    users: list[str] = []

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/demo", response_class=HTMLResponse)
    def demo() -> str:
        rows = "".join(f"<li>{html.escape(email)}</li>" for email in users)
        return f"""<!doctype html><html><body><h1>User Management</h1>
<form method='post' action='/demo/users'><label for='email'>Email</label>
<input id='email' name='email' type='email' required><button type='submit'>Create user</button></form>
<p id='message'>Users</p><ul>{rows}</ul></body></html>"""

    @app.post("/demo/users", response_class=HTMLResponse)
    def create_user(email: str = Form(...)) -> str:
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(status_code=422, detail="Invalid email address")
        if os.environ.get("HOLOQA_DEMO_FAILURE") == "1":
            raise HTTPException(status_code=500, detail="Simulated server failure")
        if email in users:
            return demo().replace("<p id='message'>Users</p>", "<p id='message'>User already exists</p>")
        users.append(email)
        return demo().replace("<p id='message'>Users</p>", "<p id='message'>User created successfully</p>")

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_demo_app(), host="0.0.0.0", port=int(os.environ.get("HOLOQA_DEMO_PORT", "8765")))


if __name__ == "__main__":
    main()
