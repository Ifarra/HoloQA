"""UI test transport peer: real engine/adapter, no model calls or shell actions."""
import json
from pathlib import Path
import sys

if "--peer" in sys.argv:
    turn = 0
    def send(message):
        print(json.dumps(message), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            send({"id": message["id"], "result": {}})
        elif method == "account/read":
            send({"id": message["id"], "result": {"requiresOpenaiAuth": False}})
        elif method == "thread/start":
            send({"id": message["id"], "result": {"thread": {"id": "ui-thread"}}})
        elif method == "turn/start":
            turn += 1
            send({"id": message["id"], "result": {"turn": {"id": f"ui-turn-{turn}"}}})
            if turn == 1:
                send({"id": 900, "method": "item/commandExecution/requestApproval",
                      "params": {"threadId": "ui-thread", "turnId": f"ui-turn-{turn}", "itemId": "ui-command", "command": "echo fixture only", "cwd": str(Path.cwd())}})
        elif method == "turn/interrupt":
            send({"id": message["id"], "result": {}})
            send({"method": "turn/completed", "params": {"threadId": "ui-thread",
                  "turn": {"id": f"ui-turn-{turn}", "status": "interrupted"}}})
    sys.exit(0)

from holoqa import desktop
from holoqa.desktop_codex import CodexSession
original_which = desktop.shutil.which
desktop.shutil.which = lambda name: "fixture-browser" if name == "agent-browser" else original_which(name)
desktop.CodexSession = lambda cwd, emit, tool=None: CodexSession(
    cwd, emit, tool, command=[sys.executable, "-u", str(Path(__file__).resolve()), "--peer"])
desktop.main()
