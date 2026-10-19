"""Regression coverage for the second resource/correctness audit."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from holoqa import agent, browser, browser_driver, observe, plan
from holoqa import run as runs
from holoqa.mcp import holoqa_browser, holoqa_observe, _cleanup_managed_runs
from holoqa.storage import atomic_json, file_lock
from holoqa.tui import HoloQATui
from tests.test_browser_lifecycle import run


def spec(tmp_path, code):
    return agent.LaunchSpec([sys.executable, '-u', '-c', code], dict(os.environ), tmp_path/'config')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX pipe/group reproduction')
def test_closed_streams_do_not_terminate_work(run, tmp_path):
    marker = tmp_path/'finished'
    code = f'import os,time; os.close(1); os.close(2); time.sleep(.3); open({str(marker)!r},"w").write("ok")'
    request = agent.AgentRunRequest('codex', tmp_path)
    assert agent._stream_process(spec(tmp_path, code), request, run) == 0
    assert marker.read_text() == 'ok'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX pipe/group reproduction')
def test_deadline_applies_after_parent_exit(run, tmp_path):
    code = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'
    request = agent.AgentRunRequest('codex', tmp_path)
    start = time.monotonic()
    result = agent._stream_process(spec(tmp_path, code), request, run, deadline=start+.3)
    assert result == 130 and result.reason == 'timed_out'
    assert time.monotonic()-start < 3


def test_cancel_reason_survives_timeout_setting(run, tmp_path, monkeypatch):
    event = threading.Event(); event.set()
    monkeypatch.setattr(agent, 'provider_binary', lambda _: sys.executable)
    monkeypatch.setattr(agent, 'provider_info', lambda _: {'version': 'fixture'})
    with patch.object(agent.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
        result = agent.launch(agent.AgentRunRequest('codex', tmp_path, cancel_event=event), run, timeout_s=30)
    assert result['agent']['state'] == 'interrupted'


def test_large_output_record_is_refused(run, tmp_path):
    request = agent.AgentRunRequest('codex', tmp_path, output_callback=lambda *a: None)
    with pytest.raises(agent.AgentError, match='65536'):
        agent._stream_process(spec(tmp_path, 'print("x"*1000000)'), request, run)


def test_cancelled_round_is_not_restarted(run, tmp_path, monkeypatch):
    calls=[]
    def launch(*args, **kwargs):
        calls.append(1)
        return {'agent': {'state': 'interrupted'}, 'holoqa': run.status(run.pinned_plan())}
    monkeypatch.setattr(agent, 'launch', launch)
    agent.run_until_done(agent.AgentRunRequest('codex', tmp_path), run, max_rounds=5)
    assert calls == [1]


def test_execution_lease_rejects_concurrent_run(run, tmp_path):
    with file_lock(run.dir/'.execution.lock'):
        with pytest.raises(agent.AgentError, match='active execution'):
            agent.launch(agent.AgentRunRequest('codex', tmp_path), run)


def driver_env(run, monkeypatch):
    names=browser.sessions(run)
    env={**os.environ, 'HOLOQA_RUN_DIR': str(run.dir), 'HOLOQA_BROWSER_BINARY': 'fixture',
         'HOLOQA_BROWSER_SESSIONS': json.dumps(names), 'AGENT_BROWSER_SESSION': names['admin']}
    return env, names


def test_session_budget_and_releasing_idle_actor(run, monkeypatch):
    env,names=driver_env(run, monkeypatch); env['HOLOQA_MAX_BROWSER_SESSIONS']='1'
    def raw(args, env, **kwargs):
        return json.dumps({'data': {'tabs': [{}]}}).encode() if args[0]=='tab' else b'ok'
    monkeypatch.setattr(browser_driver,'raw',raw)
    browser_driver.execute(['open','http://fixture'], env)
    other={**env,'AGENT_BROWSER_SESSION': names['anon']}
    with pytest.raises(ValueError,match='session limit'):
        browser_driver.execute(['open'], other)
    browser_driver.execute(['close'],env)
    browser_driver.execute(['open'],other)
    state=json.loads((run.out_dir/'browser-sessions.json').read_text())
    assert state['active']==[names['anon']]


def test_tab_limit_refuses_new_tab_and_closes_popup_overflow(run, monkeypatch):
    env,_=driver_env(run,monkeypatch); env['HOLOQA_MAX_BROWSER_TABS']='1'
    calls=[]; count=1
    def raw(args, env, **kwargs):
        calls.append(args)
        return json.dumps({'data': {'tabs': [{}]*count}}).encode() if args[0]=='tab' else b'ok'
    monkeypatch.setattr(browser_driver,'raw',raw)
    with pytest.raises(ValueError,match='Tab limit'):
        browser_driver.execute(['tab','new'],env)
    assert ['tab','new'] not in calls
    count=2
    with pytest.raises(ValueError,match='session closed'):
        browser_driver.execute(['click','#popup'],env)
    assert ['close'] in calls
    assert json.loads((run.out_dir/'browser-sessions.json').read_text())['active']==[]


def test_driver_rejects_unowned_sessions_and_overrides(run, monkeypatch):
    env,_=driver_env(run,monkeypatch)
    with pytest.raises(ValueError,match='owned session'):
        browser_driver.execute(['open'],{**env,'AGENT_BROWSER_SESSION':'default'})
    with pytest.raises(ValueError,match='Unowned'):
        browser_driver.execute(['open','--session=other'],env)


def test_cleanup_does_not_start_unused_sessions(run, monkeypatch):
    monkeypatch.setattr(browser_driver,'raw',lambda *a,**k: pytest.fail('unused session started'))
    assert browser.cleanup(run)==[]


def test_tui_retains_tail_and_writes_full_journal(tmp_path):
    app=HoloQATui(); app.log_path=tmp_path/'agent.log'
    with patch.object(app,'_render_transcript'):
        for i in range(2500): app._append_log(f'event-{i}')
    assert len(app._log_lines)==len(app._transcript_entries)==2000
    saved=app.log_path.read_text().splitlines()
    assert len(saved)==2500 and 'event-0' in saved[0] and 'event-2499' in saved[-1]
    app._persist_log()
    assert len(app.log_path.read_text().splitlines())==2500


def test_tui_shutdown_cancels_and_awaits_worker():
    app=HoloQATui()
    app._worker=threading.Thread(target=lambda: app._cancel_event.wait(5))
    app._worker.start()
    asyncio.run(app._shutdown_agent())
    assert not app._worker.is_alive() and app._cancel_event.is_set()


def test_unverified_status_is_never_release(run):
    data=run.read()
    for step in data['steps'].values(): step['verdict']='PASS'
    run._write(data)
    result=run.status(run.pinned_plan())
    assert result['integrity']=='unverified' and result['decision']=='HOLD'


def test_sse_redaction_and_duration_limit(run, monkeypatch):
    monkeypatch.setattr(observe,'evaluate',lambda *a,**k: {'events':[{'data':'{"access_token":"fixture-secret"}'}],'count':1})
    destination=run.evidence_dir/'sse.json'
    observe.sse(destination,'http://fixture')
    assert 'fixture-secret' not in destination.read_text()
    with pytest.raises(observe.CaptureError, match='60'):
        observe.sse(destination,'http://fixture',seconds=1000)


def test_download_waits_and_consumes_fresh_payload(run, monkeypatch):
    values=iter([None, None, {'id':'fresh','data':base64.b64encode(b'fresh').decode(),'type':'text/plain'}])
    monkeypatch.setattr(observe,'evaluate',lambda *a,**k: next(values))
    monkeypatch.setattr(observe.time,'sleep',lambda _:None)
    destination=run.evidence_dir/'download.bin'
    assert observe.collect_download(destination)['bytes']==5
    assert destination.read_bytes()==b'fresh'


def test_atomic_write_failure_preserves_old_json(tmp_path, monkeypatch):
    path=tmp_path/'state.json'; path.write_text('{"old":true}')
    monkeypatch.setattr('holoqa.storage.os.replace',lambda *a: (_ for _ in ()).throw(OSError('fixture')))
    with pytest.raises(OSError): atomic_json(path, {'new':True})
    assert json.loads(path.read_text())=={'old':True}
    assert list(tmp_path.iterdir())==[path]


def test_workspace_allocations_are_process_safe(tmp_path):
    source=tmp_path/'p.yaml'; source.write_text('meta: {app: concurrent}\nsteps:\n - id: A\n   title: fixture\n   expect: [{screenshot: required}]\n')
    workspace=tmp_path/'workspace'
    code='from holoqa.workspace import Workspace; from holoqa.plan import load; import sys; print(Workspace(sys.argv[1]).start_run(load(sys.argv[2])).dir.name)'
    workers=[subprocess.Popen([sys.executable,'-c',code,str(workspace),str(source)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
    results=[p.communicate(timeout=20) for p in workers]
    assert all(p.returncode==0 for p in workers),results
    assert len({out.strip() for out,err in results})==4
    assert len(json.loads((workspace/'workspace.json').read_text())['runs'])==4


def test_recording_owner_rejects_other_process(run):
    run.note('A','owner')
    code='from holoqa.run import Run,GuardrailError; import sys;\ntry: Run(sys.argv[1]).note("A","other")\nexcept GuardrailError: sys.exit(7)'
    result=subprocess.run([sys.executable,'-c',code,str(run.dir)],capture_output=True,timeout=10)
    assert result.returncode==7,result.stderr


def node(script):
    if not shutil.which('node'): pytest.skip('Node is required for browser-JS fixtures')
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_download_js_rearm_clears_stale_and_consumes_once():
    script='''global.window={}; global.URL={createObjectURL:()=> 'blob:fixture'};
    global.FileReader=class {readAsDataURL(obj){this.result='data:text/plain;base64,QQ=='; this.onload();}};
    '''+f'const hook={json.dumps(observe._BLOB_HOOK_JS)}, read={json.dumps(observe._BLOB_READ_JS)};'+'''
    eval(hook); URL.createObjectURL({size:1,type:'text/plain'});
    const first=eval(read); const consumed=eval(read);
    eval(hook); const rearmed=eval(read);
    console.log(JSON.stringify({first:JSON.parse(first).data,consumed,rearmed}));'''
    assert node(script)=={'first':'QQ==','consumed':None,'rearmed':None}


def test_sse_js_counts_all_but_retains_fifty():
    script='''let finish; global.EventSource=class {constructor(){global.source=this;} close(){}};
    global.setTimeout=(fn)=>{finish=fn;return 1}; global.clearTimeout=()=>{};
    const capture='''+(observe._SSE_JS % {'url':'"http://fixture"','seconds':1})+''';
    for(let i=0;i<10000;i++)source.onmessage({data:'event'});
    finish(); capture.then(value=>console.log(value));'''
    result=node(script)
    assert result['count']==10000 and len(result['events'])==50


def test_api_js_refuses_oversize_json():
    script='''global.fetch=async()=>new Response('x'.repeat(1048577)); const capture='''+(observe._API_JS % {'method':'"GET"','url':'"http://fixture"','body':'null','headers':'{}'})+'''; capture.then(value=>console.log(value));'''
    assert 'exceeds 1 MiB' in node(script)['error']


def test_standalone_browser_tool_shares_capture_routing(run, monkeypatch):
    monkeypatch.delenv('HOLOQA_MANAGED_BROWSER',raising=False)
    monkeypatch.setattr('shutil.which',lambda *a,**k:'fixture-browser')
    envs=[]
    def execute(args, env, **kwargs):
        envs.append(env)
        if args[0]=='eval':
            return json.dumps({'url':'http://fixture','title':'page','text':'page','text_length':4}).encode()
        return b'ok'
    monkeypatch.setattr(browser_driver,'execute',execute)
    holoqa_browser(['open','http://fixture'],actor='admin',run_dir=str(run.dir))
    holoqa_observe('A','dom',run_dir=str(run.dir))
    expected=browser.sessions(run)['admin']
    assert [e['AGENT_BROWSER_SESSION'] for e in envs]==[expected,expected]
    monkeypatch.setattr(browser,'cleanup',lambda run: [])
    _cleanup_managed_runs()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows Job Object regression')
def test_windows_job_keeps_ownership_after_parent_exit(run, tmp_path):
    code = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'
    request = agent.AgentRunRequest('codex', tmp_path)
    start = time.monotonic()
    result = agent._stream_process(spec(tmp_path, code), request, run, deadline=start+1)
    assert result.reason == 'timed_out'
    assert time.monotonic()-start < 5


def test_new_run_directory_does_not_collide_in_same_second(run, tmp_path):
    request = agent.AgentRunRequest('codex', tmp_path)
    plan = run.pinned_plan()
    assert agent.new_run_directory(plan, request) != agent.new_run_directory(plan, request)


def test_ledger_retention_is_bounded_and_eviction_loses_authority(run, tmp_path):
    source=run.pinned_plan()
    evidence=run.evidence_dir/'owned.png'; evidence.write_bytes(b'fixture')
    run.attach('A',kind='screenshot',path=evidence,actor='admin')
    assert run.integrity(adopt=False)['status']=='verified'
    for i in range(17):
        other=runs.Run.create(tmp_path/f'other-{i}',source)
        other.ledger()
    assert len(runs._LEDGERS)<=16
    assert run.integrity(adopt=False)['status']=='unverified'


def test_managed_capture_refuses_a_different_run(run, tmp_path, monkeypatch):
    other=runs.Run.create(tmp_path/'other',run.pinned_plan())
    monkeypatch.setenv('HOLOQA_RUN_DIR',str(run.dir))
    monkeypatch.setenv('HOLOQA_MANAGED_BROWSER','1')
    with pytest.raises(runs.GuardrailError,match='different run'):
        holoqa_observe('A','dom',run_dir=str(other.dir))


def test_plan_browser_flags_reach_navigation_before_capture(run, tmp_path, monkeypatch):
    monkeypatch.setattr('shutil.which',lambda *a,**k:'/fixture/agent-browser')
    source=run.pinned_plan()
    source.meta.browser.ignore_https_errors=True
    source.meta.browser.args=['--disable-dev-shm-usage']
    monkeypatch.setattr(run,'pinned_plan',lambda:source)
    env={'PATH':'/fixture'}
    browser.configure(run,tmp_path/'bin',env)
    assert '--ignore-https-errors' in env['AGENT_BROWSER_ARGS']
    assert '--disable-dev-shm-usage' in env['AGENT_BROWSER_ARGS']


def test_provider_exit_130_is_not_mislabeled_as_timeout(run, tmp_path, monkeypatch):
    monkeypatch.setattr(agent,'provider_binary',lambda _:'true')
    monkeypatch.setattr(agent,'provider_info',lambda _:{'version':'fixture'})
    monkeypatch.setattr(agent,'_stream_process',lambda *a,**k:agent.StreamExit(130))
    result=agent.launch(agent.AgentRunRequest('codex',tmp_path),run,timeout_s=30)
    assert result['agent']['state']=='interrupted'


def test_initial_cli_run_receives_timeout(run, tmp_path, monkeypatch):
    from holoqa.cli import main
    received={}
    def launch(request, current_run, **kwargs):
        received.update(kwargs)
        return {'agent':{'provider':'codex','state':'completed','exit_code':0},'holoqa':current_run.status(current_run.pinned_plan())}
    monkeypatch.setattr(agent,'launch',launch)
    assert main(['agent','run',str(run.plan_pin),'--provider','codex','--cwd',str(tmp_path),'--run-dir',str(tmp_path/'cli-run'),'--timeout','17'])==0
    assert received['timeout_s']==17


@pytest.mark.anyio
async def test_tui_quit_joins_a_worker_that_emits_callbacks(run, tmp_path, monkeypatch):
    def execute(plan_path, request, **kwargs):
        while not request.cancel_event.is_set():
            request.output_callback('stdout','{"type":"progress","message":"working"}')
            request.cancel_event.wait(.01)
        return {'agent': {'state':'interrupted','exit_code':130}, 'holoqa':run.status(run.pinned_plan())}
    monkeypatch.setattr(agent,'create_and_launch',execute)
    request=agent.AgentRunRequest('codex',tmp_path)
    app=HoloQATui(plan_path=run.plan_pin,request=request,run_dir=str(tmp_path/'tui-run'))
    async with app.run_test() as pilot:
        await pilot.pause()
        await asyncio.wait_for(pilot.press('q'),5)
    assert app._cancel_event.is_set()
    assert app._worker is not None and not app._worker.is_alive()
