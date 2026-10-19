"""A deterministic app-server peer proves approval routing without paid calls."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from holoqa.desktop_codex import CodexSession, ProtocolError

PEER = r'''
import json,sys,time,subprocess
def send(v):
    print(json.dumps(v),flush=True)
for line in sys.stdin:
    m=json.loads(line);method=m.get('method')
    if method=='initialize':send({'id':m['id'],'result':{}})
    elif method=='account/read':send({'id':m['id'],'result':{'account':{'type':'apiKey'},'requiresOpenaiAuth':True}})
    elif method=='thread/start':
        assert m['params']['approvalPolicy']=='untrusted'
        assert m['params']['sandbox']=='read-only'
        send({'id':m['id'],'result':{'thread':{'id':'thread-1'}}})
    elif method=='turn/start':
        send({'id':m['id'],'result':{'turn':{'id':'turn-1'}}})
        send({'id':900,'method':'item/commandExecution/requestApproval','params':{'command':'echo check','cwd':'.','reason':'QA prerequisite'}})
    elif m.get('id')==900:
        assert m['result']['decision'] in ('accept','decline')
        send({'id':901,'method':'item/tool/call','params':{'tool':'holoqa_block','arguments':{'step_id':'A1','reason':'Fixture prerequisite unavailable'}}})
    elif m.get('id')==901:
        assert m['result']['success'] is True
        send({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
'''


def session(tmp_path, emit, tool=lambda *_:{"status":"blocked"}):
    peer=tmp_path / "fake app server.py";peer.write_text(PEER,encoding="utf-8")
    return CodexSession(tmp_path, emit, tool, command=[sys.executable,"-u",str(peer)])


@pytest.mark.parametrize("decision", ["accept","decline"])
def test_supervised_approval_requires_explicit_answer_and_tool_dispatch(tmp_path, decision):
    events=[];requests=[];ready=threading.Event()
    def emit(event,data):
        events.append((event,data))
        if event=='approval.request':ready.set()
    def tool(name,args):
        requests.append((name,args));return {"status":"blocked"}
    codex=session(tmp_path,emit,tool)
    try:
        assert codex.initialize()=='ready'
        codex.start('Execute QA',[])
        assert ready.wait(3)
        assert not codex.finished.is_set(), 'Server must wait until the user answers'
        key=next(data['id'] for event,data in events if event=='approval.request')
        with pytest.raises(ProtocolError):codex.approve(key,'acceptForSession')
        codex.approve(key,decision)
        assert codex.finished.wait(3)
        assert codex.turn_status=='completed'
        assert requests[0][0]=='holoqa_block'
        with pytest.raises(ProtocolError):codex.approve(key,decision)
    finally:codex.close()


def test_unknown_requests_and_file_writes_fail_closed(tmp_path):
    codex=session(tmp_path,lambda *_:None)
    replies=[]
    try:
        codex.send=replies.append
        codex.handle({'id':1,'method':'item/fileChange/requestApproval','params':{}})
        codex.handle({'id':2,'method':'permissions/grantUnknown','params':{}})
        assert replies[0]['result']['decision']=='decline'
        assert replies[1]['error']['code']==-32601
    finally:codex.close()


def test_stop_owns_a_child_that_outlives_its_parent(tmp_path):
    child_pid=tmp_path/'child.pid'
    peer=tmp_path/'tree.py'
    peer.write_text("import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\nPath(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(120)\n")
    codex=CodexSession(tmp_path,lambda *_:None,command=[sys.executable,str(peer),str(child_pid)])
    try:
        until=time.monotonic()+5
        while not child_pid.exists() and time.monotonic()<until:time.sleep(.01)
        assert child_pid.exists()
        pid=int(child_pid.read_text())
        codex.close()
        assert codex.process.poll() is not None
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            k.OpenProcess.restype=wintypes.HANDLE
            handle=k.OpenProcess(0x00100000,False,pid)
            if handle:
                k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
                assert k.WaitForSingleObject(handle,5000)==0
                k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle(handle)
        else:
            status=Path(f'/proc/{pid}/stat')
            # A killed orphan can be a zombie until the host init reaps it.
            until=time.monotonic()+3
            while status.exists() and status.read_text().split()[2]!='Z' and time.monotonic()<until:time.sleep(.01)
            assert not status.exists() or status.read_text().split()[2]=='Z'
    finally:codex.close()


def test_cancellation_does_not_wait_for_a_stalled_stdin_writer(tmp_path):
    codex=CodexSession(tmp_path,lambda *_:None,
        command=[sys.executable,'-c','import time;time.sleep(120)'])
    errors=[]
    def write():
        try:codex.send({'payload':'x'*(512*1024)})
        except (OSError,ProtocolError) as error:errors.append(error)
    writer=threading.Thread(target=write,daemon=True)
    writer.start()
    until=time.monotonic()+3
    while not codex.write_lock.locked() and time.monotonic()<until:time.sleep(.01)
    closer=threading.Thread(target=codex.close,daemon=True)
    closer.start();closer.join(timeout=3)
    completed=not closer.is_alive()
    if not completed:
        # Ensure a regression fails without leaving the test runner hung.
        codex.process.kill();closer.join(timeout=5)
    writer.join(timeout=3)
    assert completed, 'Cancellation waited for an unread stdin pipe'
    assert not writer.is_alive()
    assert codex.process.poll() is not None
