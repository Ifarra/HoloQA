"use client";

import * as React from "react";
import {
  ArrowUpCircleIcon,
  BarChartIcon,
  CameraIcon,
  ClipboardListIcon,
  DatabaseIcon,
  FileCodeIcon,
  FileIcon,
  FileTextIcon,
  FolderIcon,
  HelpCircleIcon,
  LayoutDashboardIcon,
  ListIcon,
  SearchIcon,
  SettingsIcon,
  UsersIcon,
  PlusIcon,
} from "lucide-react";

import { NavDocuments } from "@/components/nav-documents";
import { NavMain } from "@/components/nav-main";
import { NavSecondary } from "@/components/nav-secondary";
import { NavUser } from "@/components/nav-user";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";

const data = {
  user: {
    name: "Jordan Davis",
    email: "quality@holoqa.dev",
    avatar: "",
  },
  navMain: [
    {
      title: "Overview",
      url: "/",
      icon: LayoutDashboardIcon,
    },
    {
      title: "Projects",
      url: "/projects",
      icon: ListIcon,
    },
    {
      title: "Runs",
      url: "/runs",
      icon: BarChartIcon,
    },
    {
      title: "Findings",
      url: "/findings",
      icon: FolderIcon,
    },
    {
      title: "Reports",
      url: "/reports",
      icon: UsersIcon,
    },
  ],
  navClouds: [
    {
      title: "Requirements",
      icon: CameraIcon,
      isActive: true,
      url: "#",
      items: [
        {
          title: "Active Proposals",
          url: "#",
        },
        {
          title: "Archived",
          url: "#",
        },
      ],
    },
    {
      title: "Assistant",
      icon: FileTextIcon,
      url: "#",
      items: [
        {
          title: "Active Proposals",
          url: "#",
        },
        {
          title: "Archived",
          url: "#",
        },
      ],
    },
    {
      title: "Settings",
      icon: FileCodeIcon,
      url: "#",
      items: [
        {
          title: "Active Proposals",
          url: "#",
        },
        {
          title: "Archived",
          url: "#",
        },
      ],
    },
  ],
  navSecondary: [
    {
      title: "Settings",
      url: "/settings",
      icon: SettingsIcon,
    },
    {
      title: "Get Help",
      url: "/assistant",
      icon: HelpCircleIcon,
    },
    {
      title: "Search",
      url: "/projects",
      icon: SearchIcon,
    },
  ],
  documents: [
    {
      name: "Requirements",
      url: "/requirements",
      icon: DatabaseIcon,
    },
    {
      name: "Reports",
      url: "/reports",
      icon: ClipboardListIcon,
    },
    {
      name: "Assistant",
      url: "/assistant",
      icon: FileIcon,
    },
  ],
};

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  return (
    <Sidebar collapsible="offcanvas" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              className="data-[slot=sidebar-menu-button]:!p-1.5"
            >
              <a href="/">
                <span className="flex size-7 items-center justify-center border-2 border-primary text-primary">
                  <PlusIcon className="size-4" aria-hidden="true" />
                </span>
                <span className="text-base font-semibold tracking-tight">
                  HOLOQA <span className="text-primary">/ LABS</span>
                </span>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={data.navMain} />
        <NavDocuments items={data.documents} />
        <NavSecondary items={data.navSecondary} className="mt-auto" />
      </SidebarContent>
      <SidebarFooter>
        <NavUser user={data.user} />
      </SidebarFooter>
    </Sidebar>
  );
}
