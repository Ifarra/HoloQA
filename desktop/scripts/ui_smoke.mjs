// Test the React UI against the real Python stdio engine. Only the native
// folder picker / IPC transport are substituted; there are no fake QA results.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import assert from 'node:assert/strict';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(desktop, '..');
const temp = await mkdtemp(join(tmpdir(), 'holoqa-ui-'));
const project = join(temp, 'storefront é with spaces');
await mkdir(project);
const source = `meta:\n  app: storefront\n  base_url: http://localhost:3000\nstages:\n  - id: A\n    title: Discovery\nsteps:\n  - id: A1\n    stage: A\n    title: Storefront is ready\n    expect:\n      - text_contains: Ready\n`;
await writeFile(join(project,'holoqa.plan.yaml'),source);
await writeFile(join(project,'package.json'),JSON.stringify({dependencies:{next:'test'},scripts:{dev:'DO NOT EXECUTE'}}));
const python = process.env.HOLOQA_DESKTOP_PYTHON || join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const engine = spawn(python,['-m','holoqa.desktop','--state-dir',join(temp,'state')],{cwd:root,stdio:['pipe','pipe','inherit']});
const pending = new Map();let id=0;
const lines=createInterface({input:engine.stdout});
lines.on('line',line=>{const m=JSON.parse(line);if(m.id){const p=pending.get(m.id);pending.delete(m.id);if(p)m.error?p.reject(new Error(m.error.message)):p.resolve(m.result);}});
const request=(method,params={})=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});engine.stdin.write(JSON.stringify({id:key,method,params})+'\n');});
const vite=spawn(process.execPath,[join(desktop,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','1421'],{cwd:desktop,stdio:'ignore'});
let browser;
try {
  for(let n=0;n<100;n++){try{const r=await fetch('http://127.0.0.1:1421');if(r.ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{}),args:process.platform==='linux'?['--no-sandbox']:[]});
  const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
  page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__nativeTest',async(command,args)=>{
    if(command==='open_project')return request('project.open',{path:project});
    if(command==='engine_request'){
      // Review state must stay coherent even with real Windows IPC latency.
      if(args.method==='plan.review')await new Promise(r=>setTimeout(r,200));
      return request(args.method,args.params);
    }
    if(command.startsWith('plugin:event|'))return 1;
    throw new Error('Unexpected native command');
  });
  await page.addInitScript(()=>{
    globalThis.isTauri=true;
    window.__TAURI_INTERNALS__={invoke:(command,args)=>window.__nativeTest(command,args),transformCallback:()=>1,unregisterCallback:()=>{}};
    window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
  });
  await page.goto('http://127.0.0.1:1421');
  await page.getByRole('button',{name:'Open project folder',exact:true}).last().click();
  await page.getByText('Your first run starts the story.',{exact:true}).waitFor();
  if(process.env.HOLOQA_SCREENSHOTS){await mkdir(process.env.HOLOQA_SCREENSHOTS,{recursive:true});await page.screenshot({path:join(process.env.HOLOQA_SCREENSHOTS,'desktop-overview.png'),fullPage:true});}
  await page.getByRole('navigation',{name:'Project pages'}).getByRole('button',{name:'Test plan',exact:true}).click();
  await page.getByRole('heading',{name:'Storefront is ready',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Start supervised run'}).isEnabled(),false);
  await page.getByRole('checkbox').check();
  // Approval must finish at the engine before editing the approved bytes.
  await page.waitForFunction(()=>{
    const button=document.querySelector('.review-footer .button');
    return button && !button.disabled;
  });
  // Edit after human review: launch must refuse before any provider/model work.
  await writeFile(join(project,'holoqa.plan.yaml'),source.replace('Ready','Changed'));
  await page.getByRole('button',{name:'Start supervised run'}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Start run',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'changed'}).waitFor();
  await page.getByRole('button',{name:'Reload',exact:false}).click();
  await page.getByText('"text_contains": "Changed"',{exact:false}).waitFor();
  assert.equal(await page.getByRole('checkbox').isChecked(),false);
  await page.getByRole('navigation',{name:'Project pages'}).getByRole('button',{name:'Run history',exact:true}).click();
  await page.getByRole('heading',{name:'No saved runs yet.'}).waitFor();
  await page.getByRole('navigation',{name:'Project pages'}).getByRole('button',{name:'Agent & target',exact:true}).click();
  await page.getByRole('button',{name:'Check connection',exact:true}).waitFor();
  await page.getByRole('button',{name:'Command palette',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Overview',exact:false}).click();
  for(const width of [1440,1000,800]){await page.setViewportSize({width,height:1050});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Layout overflow at ${width}`);}
  assert.deepEqual(errors,[]);
  console.log('UI + real stdio engine smoke passed: discovery, review, external edit refusal, history, navigation, responsive layout. No model calls.');
} finally {
  if(browser)await browser.close();
  engine.stdin.end();lines.close();vite.kill();
  await new Promise(r=>{if(engine.exitCode!==null)return r();engine.once('exit',r);setTimeout(()=>{engine.kill();r();},3000).unref();});
  await rm(temp,{recursive:true,force:true});
}
