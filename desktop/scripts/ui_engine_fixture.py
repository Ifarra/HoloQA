"""UI test transport peer: real engine/adapter, no model calls or shell actions."""
import json
from pathlib import Path
import sys

if "--peer" in sys.argv:
    turn = 0
    drafting = False
    def send(message):
        print(json.dumps(message), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            send({"id": message["id"], "result": {}})
        elif method == "account/read":
            send({"id": message["id"], "result": {"requiresOpenaiAuth": False}})
        elif method == "config/read":
            send({"id":message["id"],"result":{"config":{"mcp_servers":{}}}})
        elif method == "thread/start":
            drafting = any(tool['name'] == 'holoqa_submit_plan' for tool in message['params'].get('dynamicTools', []))
            send({"id": message["id"], "result": {"thread": {"id": "ui-thread"}}})
        elif method == "turn/start":
            turn += 1
            send({"id": message["id"], "result": {"turn": {"id": f"ui-turn-{turn}"}}})
            if drafting:
                import re
                prompt = message['params']['input'][0]['text']
                payload = json.loads(prompt.split('requirements (JSON data):\n', 1)[1].split('\nHoloQA starter', 1)[0])
                assert payload['documents'][0]['text'] == 'Acceptance: order confirmation must be visible.'
                assert 'checkout' in payload['request']
                if 'wait for cancellation' in payload['request']:
                    continue
                body = 'meta:\n  app: storefront\n  base_url: http://localhost:3000\nsteps:\n  - id: checkout\n    title: Generated checkout check\n    expect:\n      - text_contains: Order confirmed\n'
                body = body.replace('app: storefront', 'app: ' + payload['app'])
                send({'id': 901, 'method':'item/tool/call', 'params':{'threadId':'ui-thread','turnId':f'ui-turn-{turn}','tool':'holoqa_submit_plan','arguments':{'yaml':body}}})
            elif turn == 1:
                send({"id": 900, "method": "item/commandExecution/requestApproval",
                      "params": {"threadId": "ui-thread", "turnId": f"ui-turn-{turn}", "itemId": "ui-command", "command": "echo fixture only", "cwd": str(Path.cwd())}})
        elif message.get('id') == 901:
            assert message['result']['success']
            send({'method':'turn/completed','params':{'threadId':'ui-thread','turn':{'id':f'ui-turn-{turn}','status':'completed'}}})
        elif method == "turn/interrupt":
            send({"id": message["id"], "result": {}})
            send({"method": "turn/completed", "params": {"threadId": "ui-thread",
                  "turn": {"id": f"ui-turn-{turn}", "status": "interrupted"}}})
    sys.exit(0)

from holoqa import desktop
from holoqa.desktop_codex import CodexSession
original_which = desktop.shutil.which
desktop.shutil.which = lambda name: (
    "fixture-browser" if name == "agent-browser" else
    "fixture-codex" if name == "codex" else original_which(name)
)
desktop.CodexSession = lambda cwd, emit, tool=None, **kwargs: CodexSession(
    cwd, emit, tool, command=[sys.executable, "-u", str(Path(__file__).resolve()), "--peer"], **kwargs)
desktop.main()
