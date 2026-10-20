"""Resource accounting for managed agent-browser commands (not a security sandbox)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from holoqa.storage import atomic_json, file_lock

MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def limit(name: str, default: int, env: dict[str, str]) -> int:
    value = int(env.get(name, str(default)))
    if value < 1:
        raise ValueError(f'{name} must be positive')
    return value


def raw(args: list[str], env: dict[str, str], timeout: int = 120) -> bytes:
    binary = env['HOLOQA_BROWSER_BINARY']
    command = [binary, *args]
    if os.name == 'nt' and binary.lower().endswith(('.cmd', '.bat')):
        command = ['cmd', '/c', *command]
    with tempfile.TemporaryFile() as sink, tempfile.TemporaryFile() as errors:
        result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                stdout=sink, stderr=errors, timeout=timeout)
        if sink.tell() > MAX_OUTPUT_BYTES:
            raise ValueError('Browser output exceeds 8 MiB; use a narrower query')
        sink.seek(0)
        output = sink.read(MAX_OUTPUT_BYTES + 1)
        if result.returncode:
            errors.seek(0)
            detail = errors.read(2000) or output[:2000]
            raise ValueError(detail.decode('utf-8', errors='replace'))
    return output


def tabs(env: dict[str, str]) -> list:
    result = json.loads(raw(['tab', 'list', '--json'], env))
    data = result.get('data', result) if isinstance(result, dict) else result
    values = data.get('tabs') if isinstance(data, dict) else data
    if not isinstance(values, list):
        raise ValueError('Managed browsers require agent-browser tab list --json support')
    return values


def session_lock_path(run_dir: Path, session: str) -> Path:
    """Serialize commands within one browser session without blocking other actors."""
    key = hashlib.sha256(session.encode('utf-8')).hexdigest()[:20]
    return run_dir / f'.browser-session-{key}.lock'


def execute(args: list[str], env: dict[str, str] | None = None, *, timeout: int | None = None) -> bytes:
    env = dict(env or os.environ)
    timeout = timeout or int(env.get("HOLOQA_BROWSER_TIMEOUT", "120"))
    allowed = json.loads(env['HOLOQA_BROWSER_SESSIONS'])
    session = env.get('AGENT_BROWSER_SESSION', '')
    if session not in allowed.values():
        raise ValueError('Select an owned session from holoqa_run_status.browser_sessions')
    if not args or args[0].startswith('-'):
        raise ValueError('Put the browser command first; session overrides are not allowed')
    forbidden = ('--session', '--session-name', '--cdp', '--auto-connect', '--profile')
    if args[0] in {'connect', 'session', 'batch', 'mcp'} or any(
        arg == flag or arg.startswith(flag + '=') for arg in args for flag in forbidden
    ) or '--all' in args:
        raise ValueError('Unowned sessions, batch commands and external browsers are not allowed')
    run_dir = Path(env['HOLOQA_RUN_DIR'])
    registry = run_dir / 'out' / 'browser-sessions.json'
    closing = args[0] in {'close', 'quit', 'exit'}
    # Keep commands on one named session ordered, since they share page/tab
    # state. The registry lock only protects accounting; waiting for Chrome on
    # one actor must not stall a different actor in the same run.
    with file_lock(session_lock_path(run_dir, session)):
        with file_lock(run_dir / '.browser.lock'):
            state = json.loads(registry.read_text()) if registry.is_file() else {'active': []}
            active = state['active']
            if state.get('closing'):
                try:
                    # The separate OS lock outlives the marker only while a
                    # cleanup process is actually running. This also recovers
                    # stale markers without relying on reusable process IDs.
                    with file_lock(run_dir / '.browser-cleanup.lock', timeout=0):
                        pass
                except TimeoutError as error:
                    raise ValueError('Browser cleanup is in progress for this run') from error
                state.pop('closing', None)
                atomic_json(registry, state)
            if closing and session not in active:
                return b'Closed (session was not active)\n'
            if not closing and session not in active:
                if len(active) >= limit('HOLOQA_MAX_BROWSER_SESSIONS', 4, env):
                    raise ValueError('Browser session limit reached; close an idle actor session first')
                active.append(session)
                atomic_json(registry, state)
        cap = limit('HOLOQA_MAX_BROWSER_TABS', 8, env)
        growing = args[:2] in (['tab', 'new'], ['window', 'new']) or '--new-tab' in args
        # Listing tabs is another agent-browser process. Check after commands
        # that can change page topology (including popup-producing actions),
        # while read-only inspection and ordinary in-page edits avoid that
        # extra process. Explicit tab/window creation is checked both before
        # and after to catch a CLI that did not honor the requested operation.
        # Unknown/future commands are treated as potentially state-changing;
        # only known read-only inspection commands avoid the extra process.
        safe_inspection = {
            'get', 'tab', 'screenshot', 'snapshot', 'dom', 'text', 'url',
            'title', 'console', 'errors',
        }
        may_open_tab = args[0] not in safe_inspection
        try:
            if growing and len(tabs(env)) >= cap:
                raise ValueError('Tab limit reached; close an unused tab before opening another')
            action_error = None
            try:
                output = raw(args, env, timeout=timeout)
            except BaseException as error:
                action_error = error
                output = b''
            if closing:
                with file_lock(run_dir / '.browser.lock'):
                    state = json.loads(registry.read_text()) if registry.is_file() else {'active': []}
                    if session in state['active']:
                        state['active'].remove(session)
                        atomic_json(registry, state)
            elif (growing or may_open_tab) and len(tabs(env)) > cap:
                # A page can open popups without an explicit tab command. Stop
                # the owned session rather than silently discarding QA state.
                raw(['close'], env)
                with file_lock(run_dir / '.browser.lock'):
                    state = json.loads(registry.read_text()) if registry.is_file() else {'active': []}
                    if session in state['active']:
                        state['active'].remove(session)
                        atomic_json(registry, state)
                raise ValueError('Page exceeded the tab limit; session closed, reauthenticate to retry')
            if action_error:
                raise action_error
            return output
        except BaseException:
            # Keep uncertain sessions registered so cleanup retries them.
            raise


def main() -> None:
    try:
        sys.stdout.buffer.write(execute(sys.argv[1:]))
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        print(f'HoloQA browser: {error}', file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
