'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const links = [['/','Overview'],['/projects','Projects'],['/requirements','Requirements'],['/runs','Runs'],['/findings','Findings'],['/reports','Reports'],['/assistant','Assistant']] as const
export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname()
  return <div className="shell"><div className="frame"><header className="topbar"><Link className="brand" href="/"><span className="mark">+</span><span>HOLOQA / LABS</span></Link><nav className="nav">{links.map(([href,label]) => <Link key={href} className={path === href || (href !== '/' && path.startsWith(href)) ? 'active' : ''} href={href}>{label}</Link>)}</nav><Link className="button" href="/projects">New inspection ↗</Link></header>{children}</div></div>
}
