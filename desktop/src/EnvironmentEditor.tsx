import {useEffect, useState} from 'react';
import {KeyRound, Plus, Save, Trash2} from 'lucide-react';
import {events, rpc} from './bridge';

type Variable={name:string;configured:boolean;referenced:boolean};
export function EnvironmentEditor({blocked}:{blocked:boolean}) {
  const [variables,setVariables]=useState<Variable[]>([]);
  const [values,setValues]=useState<Record<string,string>>({});
  const [name,setName]=useState('');
  const [value,setValue]=useState('');
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  const refresh=async()=>setVariables(await rpc<Variable[]>('environment.list'));
  useEffect(()=>{let disposed=false;let stop=()=>{};const reload=()=>void refresh().catch(e=>setMessage(String(e)));reload();window.addEventListener('holoqa-project-change',reload);events(event=>{if(event.event==='project.opened')reload();}).then(fn=>{if(disposed)fn();else stop=fn;});return()=>{disposed=true;stop();window.removeEventListener('holoqa-project-change',reload);};},[]);
  async function save(key:string,secret:string){setBusy(true);setMessage('');try{setVariables(await rpc<Variable[]>('environment.set',{name:key,value:secret}));setValues(v=>({...v,[key]:''}));setMessage(`${key} saved for this project.`);}catch(e){setMessage(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  async function remove(key:string){setBusy(true);setMessage('');try{setVariables(await rpc<Variable[]>('environment.delete',{name:key}));setValues(v=>({...v,[key]:''}));setMessage(`${key} removed.`);}catch(e){setMessage(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  async function add(){const key=name.trim();if(!key)return;await save(key,value);setName('');setValue('');}
  return <section className="card env-editor"><div className="env-heading"><span className="square-icon blue"><KeyRound/></span><div><h2>Project environment</h2><p>Set values used by <code>${'{VARIABLE}'}</code> references in your plans. Values stay on this device and never enter plan files.</p></div><span className="env-count">{variables.length} variables</span></div>
    <div className="env-private"><KeyRound size={14}/><span>Saved values are protected by Windows user encryption. HoloQA never shows a saved secret; enter a replacement to change it.</span></div>
    {variables.length?<div className="env-list">{variables.map(item=><div className="env-row" key={item.name}><div className="env-name"><code>{item.name}</code><small>{item.referenced?'Referenced by a plan':item.configured?'Custom variable':'Not set'}</small></div><input aria-label={`New value for ${item.name}`} type="password" autoComplete="new-password" placeholder={item.configured?'Enter a replacement value':'Enter value'} value={values[item.name]||''} disabled={blocked||busy} onChange={e=>setValues(v=>({...v,[item.name]:e.target.value}))}/><button className="button secondary" disabled={blocked||busy||values[item.name]===undefined||values[item.name]===''} onClick={()=>void save(item.name,values[item.name]||'')}><Save size={14}/> Save</button>{item.configured&&<button className="icon-button danger-icon" aria-label={`Remove ${item.name}`} disabled={blocked||busy} onClick={()=>void remove(item.name)}><Trash2 size={15}/></button>}</div>)}</div>:<div className="env-empty">No variables yet. Add one here or use a <code>${'{VARIABLE_NAME}'}</code> reference in a plan.</div>}
    <div className="env-add"><input aria-label="New environment variable name" value={name} maxLength={128} placeholder="VARIABLE_NAME" disabled={blocked||busy} onChange={e=>setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g,''))}/><input aria-label="New environment variable value" type="password" autoComplete="new-password" value={value} placeholder="Variable value" disabled={blocked||busy} onChange={e=>setValue(e.target.value)}/><button className="button" disabled={blocked||busy||!name||!value} onClick={()=>void add()}><Plus size={14}/> Add variable</button></div>
    <p className="mini env-footnote">Values are loaded for the selected project when plans are reviewed and a run starts. Changing a value clears any plan approval, so review the plan again before scanning.</p>{message&&<p className="env-message" role="status">{message}</p>}
  </section>;
}
