import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

export function FeaturePage({ eyebrow, title, intro, actions, children }: { eyebrow: string; title: string; intro: string; actions?: React.ReactNode; children: React.ReactNode }) {
  return <main className="main"><section className="section" style={{ marginTop: 8 }}><div className="eyebrow">{eyebrow}</div><div className="section-head" style={{ paddingTop: 12 }}><div><h1 style={{ fontSize: "clamp(48px,7vw,96px)", maxWidth: 900 }}>{title}</h1><p className="muted" style={{ maxWidth: 650, fontSize: 15 }}>{intro}</p></div><div className="actions">{actions || <Link className="button secondary inline-flex items-center gap-1.5" href="/">Back to overview <ArrowUpRight className="size-3.5" aria-hidden="true" /></Link>}</div></div></section>{children}</main>;
}

export function ModuleCard({ eyebrow, title, body, href }: { eyebrow: string; title: string; body: string; href?: string }) {
  const content = <><div className="eyebrow">{eyebrow}</div><h3>{title}</h3><p className="muted">{body}</p>{href && <span className="link mono inline-flex items-center gap-1">Open module <ArrowUpRight className="size-3" aria-hidden="true" /></span>}</>;
  return href ? <Link className="panel" href={href}>{content}</Link> : <div className="panel">{content}</div>;
}
