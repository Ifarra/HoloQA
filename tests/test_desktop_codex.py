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
        send({'id':900,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread-1','turnId':'turn-1','command':'echo check','cwd':'.','reason':'QA prerequisite'}})
    elif m.get('id')==900:
        assert m['result']['decision'] in ('accept','decline')
        send({'id':901,'method':'item/tool/call','params':{'threadId':'thread-1','turnId':'turn-1','callId':'call-1','tool':'holoqa_block','arguments':{'step_id':'A1','reason':'Fixture prerequisite unavailable'}}})
    elif m.get('id')==901:
        assert m['result']['success'] is True
        send({'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}})
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


PAUSE_PEER = r'''
import json,sys
turn=0
declined=False
def send(v): print(json.dumps(v),flush=True)
def complete(number,status='interrupted',thread='thread-1'):
    send({'method':'turn/completed','params':{'threadId':thread,'turn':{'id':f'turn-{number}','status':status}}})
for line in sys.stdin:
    m=json.loads(line);method=m.get('method')
    if method=='initialize':send({'id':m['id'],'result':{}})
    elif method=='account/read':send({'id':m['id'],'result':{'requiresOpenaiAuth':False}})
    elif method=='thread/start':send({'id':m['id'],'result':{'thread':{'id':'thread-1'}}})
    elif method=='turn/start':
        assert m['params']['threadId']=='thread-1'
        turn+=1
        if turn==2:complete(1)  # Arrives before the new turn/start reply.
        send({'id':m['id'],'result':{'turn':{'id':f'turn-{turn}'}}})
        if turn==1:
            send({'id':900,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread-1','turnId':'turn-1','command':'echo pending'}})
        else:
            complete(1)  # Stale old-turn completion after the reply.
            complete(2,thread='different-thread')
    elif m.get('id')==900:
        assert m['result']['decision']=='decline';declined=True
    elif method=='turn/interrupt':
        assert declined
        assert m['params']=={'threadId':'thread-1','turnId':'turn-1'}
        send({'id':m['id'],'result':{}})
        send({'id':901,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread-1','turnId':'turn-1','command':'echo late'}})
    elif m.get('id')==901:
        assert m['result']['decision']=='decline'
        send({'method':'item/completed','params':{'item':{'type':'late-approval-declined'}}})
    elif method=='fixture/complete':complete(turn,'interrupted' if turn==1 else 'completed')
'''


def test_pause_requires_confirmation_and_resume_keeps_thread_and_process(tmp_path):
    peer=tmp_path/'pause peer.py';peer.write_text(PAUSE_PEER,encoding='utf-8')
    events=[];approval=threading.Event();late_declined=threading.Event()
    def emit(event,data):
        events.append((event,data))
        if event=='approval.request':approval.set()
        if data.get('message')=='Codex completed: late-approval-declined':late_declined.set()
    codex=CodexSession(tmp_path,emit,command=[sys.executable,'-u',str(peer)])
    try:
        assert codex.initialize()=='ready'
        codex.start('QA',[])
        assert approval.wait(3)
        key=next(data['id'] for event,data in events if event=='approval.request')
        pid=codex.process.pid
        codex.interrupt()
        assert late_declined.wait(3)
        assert not codex.finished.is_set(), 'RPC acknowledgement is not interruption confirmation'
        assert not codex.approvals
        with pytest.raises(ProtocolError):codex.approve(key,'accept')
        with pytest.raises(ProtocolError):codex.resume('Continue')
        codex.send({'method':'fixture/complete'})
        assert codex.finished.wait(3)
        assert codex.turn_status=='interrupted'
        assert not codex.disconnected
        codex.resume('Continue the same pinned QA plan')
        assert codex.process.pid==pid
        assert codex.turn_id=='turn-2'
        assert not codex.finished.is_set(), 'Old turn or wrong-thread completion must not end the resumed turn'
        assert len([1 for event,_ in events if event=='approval.request'])==1
        codex.send({'method':'fixture/complete'})
        assert codex.finished.wait(3)
        assert codex.turn_status=='completed'
    finally:codex.close()


def test_disconnected_provider_is_not_resumable(tmp_path):
    codex=CodexSession(tmp_path,lambda *_:None,command=[sys.executable,'-c','pass'])
    try:
        assert codex.finished.wait(3)
        assert codex.disconnected
        codex.pausing=True
        codex.turn_status='interrupted'
        with pytest.raises(ProtocolError):codex.resume('Continue')
    finally:codex.close()


def test_fast_completion_before_start_reply_is_not_lost(tmp_path):
    codex=CodexSession(tmp_path,lambda *_:None,
        command=[sys.executable,'-c','import time;time.sleep(120)'])
    try:
        codex.thread_id='thread-1'
        def request(method,params):
            assert method=='turn/start'
            codex.handle({'method':'turn/completed','params':{
                'threadId':'thread-1','turn':{'id':'turn-fast','status':'completed'}}})
            return {'turn':{'id':'turn-fast'}}
        codex.request=request
        codex._start_turn('QA')
        assert codex.finished.is_set()
        assert codex.turn_status=='completed'
        codex.handle({'method':'turn/completed','params':{
            'threadId':'thread-1','turn':{'id':'turn-fast','status':'interrupted'}}})
        assert codex.turn_status=='completed', 'Duplicate completion cannot change a terminal turn'
    finally:codex.close()


def test_interruption_discards_queued_tools_before_resume(tmp_path):
    entered, release, interrupted = threading.Event(), threading.Event(), threading.Event()
    calls, replies, errors = [], [], []
    def tool(name,args):
        calls.append(args['number'])
        if args['number']==1:
            entered.set()
            assert release.wait(5)
        return {'ok':True}
    codex=CodexSession(tmp_path,lambda *_:None,tool,
        command=[sys.executable,'-c','import time;time.sleep(120)'])
    codex.send=replies.append
    codex.request=lambda *args,**kwargs: {'turn':{'id':'turn-2'}}
    codex.thread_id,codex.turn_id='thread-1','turn-1'
    def interrupt():
        try:assert codex.interrupt() is True
        except Exception as error:errors.append(error)
        finally:interrupted.set()
    try:
        codex.handle({'id':1,'method':'item/tool/call','params':{'threadId':'thread-1','turnId':'turn-1','callId':'call-fixture','tool':'qa','arguments':{'number':1}}})
        assert entered.wait(3)
        codex.handle({'id':2,'method':'item/tool/call','params':{'threadId':'thread-1','turnId':'turn-1','callId':'call-fixture','tool':'qa','arguments':{'number':2}}})
        worker=threading.Thread(target=interrupt,daemon=True);worker.start()
        until=time.monotonic()+3
        while not codex.pausing and time.monotonic()<until:time.sleep(.01)
        assert codex.pausing
        assert not interrupted.is_set(), 'Pause must drain a callback accepted before interruption'
        release.set()
        assert interrupted.wait(3)
        assert not errors
        codex.handle({'method':'turn/completed','params':{
            'threadId':'thread-1','turn':{'id':'turn-1','status':'interrupted'}}})
        codex.resume('Continue QA')
        until=time.monotonic()+3
        while not any(reply.get('id')==2 for reply in replies) and time.monotonic()<until:time.sleep(.01)
        assert calls==[1]
        assert next(reply for reply in replies if reply.get('id')==2)['result']['success'] is False
        # This old request arrives after resume, so enqueue generation alone
        # would accidentally classify it as part of the fresh turn.
        codex.handle({'id':3,'method':'item/tool/call','params':{
            'threadId':'thread-1','turnId':'turn-1','callId':'old-call','tool':'qa','arguments':{'number':3}}})
        assert next(reply for reply in replies if reply.get('id')==3)['result']['success'] is False
        codex.handle({'id':4,'method':'item/tool/call','params':{
            'threadId':'thread-1','turnId':'turn-2','callId':'new-call','tool':'qa','arguments':{'number':4}}})
        until=time.monotonic()+3
        while not any(reply.get('id')==4 for reply in replies) and time.monotonic()<until:time.sleep(.01)
        assert next(reply for reply in replies if reply.get('id')==4)['result']['success'] is True
        assert calls==[1,4]
    finally:
        release.set()
        codex.close()


def test_tool_dispatch_queue_is_bounded(tmp_path):
    entered, release = threading.Event(), threading.Event()
    replies=[]
    def tool(*_):
        entered.set()
        assert release.wait(5)
        return {'ok':True}
    codex=CodexSession(tmp_path,lambda *_:None,tool,
        command=[sys.executable,'-c','import time;time.sleep(120)'])
    codex.send=replies.append
    codex.thread_id,codex.turn_id='thread-1','turn-1'
    try:
        for request_id in range(20):
            codex.handle({'id':request_id,'method':'item/tool/call','params':{'threadId':'thread-1','turnId':'turn-1','callId':'call-fixture','tool':'qa','arguments':{}}})
        assert entered.wait(3)
        assert len(replies)==12, 'Only eight outstanding callbacks may be accepted'
        assert all(reply['result']['success'] is False for reply in replies)
    finally:
        release.set()
        codex.close()


def test_pause_losing_race_to_natural_completion_does_not_interrupt(tmp_path):
    codex=CodexSession(tmp_path,lambda *_:None,
        command=[sys.executable,'-c','import time;time.sleep(120)'])
    try:
        codex.turn_status='completed'
        codex.finished.set()
        assert codex.interrupt() is False
        assert not codex.pausing
    finally:codex.close()


def test_fast_tool_and_approval_wait_for_start_reply_identity(tmp_path):
    calls,replies,events=[],[],[]
    called=threading.Event()
    def tool(name,args):
        calls.append((name,args));called.set();return {'ok':True}
    codex=CodexSession(tmp_path,lambda event,data:events.append((event,data)),tool,
        command=[sys.executable,'-c','import time;time.sleep(120)'])
    codex.thread_id='thread-1'
    codex.send=replies.append
    def request(method,params):
        codex.handle({'id':1,'method':'item/tool/call','params':{
            'threadId':'thread-1','turnId':'turn-fast','callId':'fast-call','tool':'qa','arguments':{}}})
        codex.handle({'id':2,'method':'item/commandExecution/requestApproval','params':{
            'threadId':'thread-1','turnId':'turn-fast','command':'echo current'}})
        codex.handle({'id':3,'method':'item/tool/call','params':{
            'threadId':'thread-1','turnId':'old-turn','callId':'old-call','tool':'qa','arguments':{}}})
        assert not calls and not events, 'Unconfirmed turn identity must not execute or prompt'
        return {'turn':{'id':'turn-fast'}}
    codex.request=request
    try:
        codex._start_turn('QA')
        assert called.wait(3)
        assert len(calls)==1
        assert next(reply for reply in replies if reply.get('id')==3)['result']['success'] is False
        assert len([1 for event,_ in events if event=='approval.request'])==1
        codex.handle({'id':4,'method':'item/commandExecution/requestApproval','params':{
            'threadId':'thread-1','turnId':'old-turn','command':'echo stale'}})
        assert next(reply for reply in replies if reply.get('id')==4)['result']['decision']=='decline'
        codex.handle({'id':5,'method':'item/tool/call','params':{'tool':'qa','arguments':{}}})
        assert next(reply for reply in replies if reply.get('id')==5)['result']['success'] is False
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


def test_approval_answer_cannot_be_sent_after_interruption(tmp_path, monkeypatch):
    codex = session(tmp_path, lambda *_: None)
    entered, release, interrupted = threading.Event(), threading.Event(), threading.Event()
    order, errors = [], []
    codex.thread_id, codex.turn_id = 'thread-1', 'turn-1'
    codex.approvals['one-action'] = 900
    def send(message):
        entered.set()
        assert release.wait(3)
        order.append('answer')
    def request(method, params, **kwargs):
        order.append(method)
        interrupted.set()
        return {}
    monkeypatch.setattr(codex, 'send', send)
    monkeypatch.setattr(codex, 'request', request)
    def answer():
        try: codex.approve('one-action', 'accept')
        except Exception as error: errors.append(error)
    writer = threading.Thread(target=answer)
    pauser = threading.Thread(target=codex.interrupt)
    try:
        writer.start()
        assert entered.wait(3)
        pauser.start()
        assert not interrupted.wait(.1), 'Interruption overtook an in-flight approval response'
        release.set()
        writer.join(3); pauser.join(3)
        assert not writer.is_alive() and not pauser.is_alive()
        assert not errors
        assert order == ['answer', 'turn/interrupt']
        with pytest.raises(ProtocolError, match='paused'):
            codex.approve('one-action', 'accept')
    finally:
        release.set()
        writer.join(3); pauser.join(3)
        codex.close()
