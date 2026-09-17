import Link from "next/link";
import { ArrowUpRight, Search } from "lucide-react";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

export function SiteHeader() {
  return (
    <header className="group-has-data-[collapsible=icon]/sidebar-wrapper:h-12 flex h-12 shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear">
      <div className="flex w-full items-center gap-1 px-4 lg:gap-2 lg:px-6">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mx-2 data-[orientation=vertical]:h-4" />
        <h1 className="text-sm font-semibold uppercase tracking-[0.16em]">Projects / Dashboard</h1>
        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-2 border bg-background px-3 py-1.5 text-xs text-muted-foreground md:flex">
            <Search className="size-3.5" aria-hidden="true" />
            <span>Search projects, workspaces, or inspections...</span>
            <kbd className="ml-4 border px-1.5 py-0.5 font-mono text-[10px]">⌘ K</kbd>
          </div>
          <Link href="/projects/new" className="inline-flex h-8 items-center gap-1.5 bg-primary px-3 text-xs font-semibold uppercase tracking-[0.08em] text-primary-foreground hover:bg-primary/90">
            New inspection <ArrowUpRight className="size-3.5" aria-hidden="true" />
          </Link>
        </div>
      </div>
    </header>
  );
}
