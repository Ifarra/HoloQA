import Link from "next/link";

export default function ProjectSetupRemoved() {
  return <main className="dashboard"><section className="section"><div className="panel"><div className="eyebrow">MCP-controlled setup</div><h1>PROJECT INITIALIZATION MOVED</h1><p className="muted">Projects are initialized by the connected remote HoloQA MCP server. Mount the application under /workspace on the Docker host, then ask your AI coder to call holoqa_project_inspect and holoqa_initialize_project.</p><Link className="button secondary" href="/create-test">View remote MCP workflow ↗</Link></div></section></main>;
}
