import {useEffect, useRef, useState} from 'react';
import {FileText, Paperclip, Sparkles, X, Square, FolderOpen} from 'lucide-react';
import {events, pickPlanFiles, rpc, type Draft, type Plan} from './bridge';

const empty:Draft={state:'idle',yaml:'',error:'',summary:null,attachments:[]};
const isActive=(state:string)=>['attaching','starting','generating','cancelling','finishing'].includes(state);
export function PlanComposer({app,target,blocked,onSaved,onStarter}:{app:string;target:string;blocked:boolean;onSaved:(plan:Plan)=>Promise<void>;onStarter:()=>void}) {
  const [draft,setDraft]=useState<Draft>(empty);
  const [name,setName]=useState(app);
  const [url,setUrl]=useState(target);
  const urlEdited=useRef(false);
  useEffect(()=>{if(!urlEdited.current)setUrl(target);},[target]);
  const [prompt,setPrompt]=useState('');
  const [yaml,setYaml]=useState('');
  const serverYaml=useRef('');
  const [filename,setFilename]=useState('generated.plan.yaml');
  const [error,setError]=useState('');
  const [working,setWorking]=useState(false);
  const generating=isActive(draft.state);
  const disabled=working||generating||blocked;
  useEffect(()=>{
    let disposed=false;let stop=()=>{};
    const adopt=(value:Draft)=>{if(!disposed){setDraft(value);if(serverYaml.current!==value.yaml){setYaml(value.yaml);serverYaml.current=value.yaml;}}};
    events(event=>{if(event.event==='plan.draft.updated')adopt(event.data as unknown as Draft);}).then(fn=>{if(disposed)fn();else stop=fn;});
    rpc<Draft>('plan.draft.snapshot').then(adopt).catch(e=>{if(!disposed)setError(String(e));});
    return()=>{disposed=true;stop();};
  },[]);
  async function action(fn:()=>Promise<void>){setWorking(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setWorking(false);}}
  const pick=(kind:'documents'|'plan')=>action(async()=>{const result=await pickPlanFiles(kind);if(result){setDraft(result);if(kind==='plan'){setYaml(result.yaml);serverYaml.current=result.yaml;}}});
  const generate=()=>action(async()=>{const result=await rpc<Draft>('plan.draft.start',{app:name,url,prompt,attachments:draft.attachments.map(d=>d.id)});setDraft(result);setYaml(result.yaml);serverYaml.current=result.yaml;});
  const save=()=>action(async()=>onSaved(await rpc<Plan>('plan.draft.save',{yaml,filename})));
  return <section className="plan-composer">
    <div className="page-heading"><div><h1>Create a test plan</h1><p>Describe the journeys. Bring your requirements. Review the draft before running.</p></div><button className="button secondary" disabled={disabled} onClick={()=>void pick('plan')}><FolderOpen size={15}/> Import plan file</button></div>
    {blocked&&<div className="banner">Stop the active QA run before creating a plan.</div>}
    {(error||draft.error)&&<div className="error" role="alert">{error||draft.error}</div>}
    <div className="authoring-grid">
      <section className="card author-request"><span className="eyebrow">DESCRIBE WHAT TO TEST</span><h2>Your requirements, in your words.</h2>
        <div className="author-fields"><label>Application name<input value={name} disabled={disabled} maxLength={64} onChange={e=>setName(e.target.value)}/></label><label>Application URL<input value={url} disabled={disabled} onChange={e=>{urlEdited.current=true;setUrl(e.target.value);}} placeholder="http://localhost:3000"/></label></div>
        <label>What should this plan cover?<textarea aria-label="What should this plan cover?" className="requirements-input" value={prompt} disabled={disabled} maxLength={16000} onChange={e=>setPrompt(e.target.value)} placeholder="Test signup, login, and checkout. Include invalid inputs, an empty cart, and order confirmation. Use the attached specification for acceptance criteria."/></label>
        <div className="attachment-heading"><h3>Reference documents <span className="mini">{draft.attachments.length} / 8</span></h3><button className="button secondary" disabled={disabled||draft.attachments.length>=8} onClick={()=>void pick('documents')}><Paperclip size={14}/> Attach documents</button></div>
        <p className="mini">TXT, Markdown, PDF, DOCX · 5 MiB per file · 15 MiB and 100,000 extracted characters total. Text only; scanned PDFs need OCR. DOCX body text is included; images, comments, headers, and footers are not.</p>
        <ul className="attachment-list">{draft.attachments.map(document=><li key={document.id}><FileText size={16}/><span><strong>{document.name}</strong><small>{Math.ceil(document.size/1024)} KiB · {document.characters.toLocaleString()} characters extracted</small></span><button aria-label={`Remove ${document.name}`} disabled={disabled} onClick={()=>void action(async()=>setDraft(await rpc<Draft>('plan.attachment.remove',{id:document.id})))}><X size={15}/></button></li>)}</ul>
        <p className="author-disclosure">Generating sends your request and the extracted text of these documents to your configured Codex provider. Attach only content you intend to share. Originals stay in place; attachment text is held in memory for this project session.</p>
        <p className="mini">Generating again replaces the proposal. Save a draft you want to keep first.</p><div className="actions">{generating?<button className="button danger" disabled={draft.state==='cancelling'||working} onClick={()=>void action(async()=>setDraft(await rpc<Draft>('plan.draft.cancel')))}><Square size={14}/> {draft.state==='attaching'?'Cancel attachments':'Cancel generation'}</button>:<button className="button" disabled={disabled||!prompt.trim()||!name.trim()||!url.trim()} onClick={()=>void generate()}><Sparkles size={15}/> Generate plan with Codex</button>}</div>
        <p className="mini">Already have a plan? Import its YAML above, or open a plan discovered in this project. You can also <button className="text-button" disabled={disabled} onClick={onStarter}>create a starter plan</button>.</p>
      </section>
      <section className="card author-preview"><div className="row"><span className="square-icon"><FileText/></span><div><h2>Proposed plan</h2><p className="mini" role="status">{draft.state==='attaching'?'Extracting document text…':generating?'Codex is drafting — no scan is running.':draft.state==='ready'?'Draft ready. Edit, save, then review every check.':draft.state==='cancelled'?'Generation cancelled. Your existing plans are unchanged.':'A generated or imported plan will appear here.'}</p></div></div>
        {yaml?<><label>Plan YAML<textarea aria-label="Plan YAML" className="yaml-input" spellCheck={false} value={yaml} disabled={generating||working||blocked} onChange={e=>setYaml(e.target.value)}/></label>{draft.summary&&yaml===draft.yaml&&<div className="banner">{draft.summary.checks} checks · {draft.summary.app}<br/>{draft.summary.target}</div>}{draft.summary&&yaml===draft.yaml&&draft.summary.warnings.map((warning,i)=><p className="mini author-warning" key={i}>{warning}</p>)}<label>Save as<input value={filename} disabled={disabled} onChange={e=>setFilename(e.target.value)} placeholder="checkout.plan.yaml"/></label><p className="mini">Saves a new file in this project. Existing files are never overwritten. Edits are validated again on save; saving does not approve or execute the plan.</p><button className="button" disabled={disabled||draft.state!=='ready'||!yaml.trim()} onClick={()=>void save()}>Save and review <span aria-hidden="true">→</span></button></>:<div className="draft-empty"><Sparkles size={32}/><h3>{draft.state==='attaching'?'Reading your reference documents…':generating?'Turning requirements into checks…':'From intent to reviewable checks.'}</h3><p>Codex proposes the plan. HoloQA validates its structure. You decide what to run.</p></div>}
      </section>
    </div>
  </section>;
}
