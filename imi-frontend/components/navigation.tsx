"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useState, useEffect, useMemo, Suspense } from "react";
import * as Collapsible from "@radix-ui/react-collapsible";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import BasePathLink, { KNOWN_ROUTES } from "@/lib/utils/links";
import { LoginButton } from "@/components/auth/LoginButton";
import { UserMenu } from "@/components/auth/UserMenu";
import { useAuth } from "@/lib/hooks/useAuth";
import { useDomain } from "@/contexts/DomainContext";
import { getEntityIcon } from "@/lib/utils/entity-icons";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";
import {
  MessageSquare,
  Search,
  Brain,
  Share2,
  User,
  ChevronDown,
  Menu,
  Sparkles,
  Zap,
  Scale,
  Home,
  Settings,
} from "lucide-react";

/**
 * Detects the base path from the current URL at runtime.
 * This matches the logic in BasePathLink for consistency.
 */
function detectBasePath(): string {
  if (typeof window === 'undefined') return '';

  const pathname = window.location.pathname;

  // Find the first known route in the pathname
  for (const route of KNOWN_ROUTES) {
    const index = pathname.indexOf(route);
    if (index > 0) {
      // Extract everything before the known route as the base path
      return pathname.substring(0, index).replace(/\/$/, '');
    }
  }

  // If at root of a subpath (e.g., /my-instance/)
  const segments = pathname.split('/').filter(Boolean);
  if (segments.length === 1 && !KNOWN_ROUTES.some(r => r.startsWith('/' + segments[0]))) {
    return '/' + segments[0];
  }

  return '';
}

/**
 * Hook to detect base path on client-side
 */
function useDetectedBasePath(): string {
  const [basePath, setBasePath] = useState('');

  useEffect(() => {
    setBasePath(detectBasePath());
  }, []);

  return basePath;
}

/**
 * Hook that builds navigation groups with domain-aware labels.
 * The knowledge_base group is dynamically generated from domain entity types —
 * each entity type becomes its own nav item linking to /explorer?type={key}.
 *
 * @param reviewCount - Number of items awaiting review; null until first load.
 *   Passed through to the Constitution item badge.
 */
function useNavGroups(reviewCount: number | null) {
  const { getNavLabel, getGroupLabel, domainConfig, getEntityDisplayName } = useDomain();

  return useMemo(() => {
    // Build knowledge_base items dynamically from domain entity types
    const knowledgeBaseItems: {
      name: string;
      href: string;
      icon: React.ComponentType<{ className?: string }>;
      description: string;
      requiresAuth?: boolean;
      badge?: number;
    }[] = [];

    if (domainConfig?.entities && Object.keys(domainConfig.entities).length > 0) {
      for (const [entityKey, entity] of Object.entries(domainConfig.entities)) {
        knowledgeBaseItems.push({
          name: getEntityDisplayName(entityKey, true),
          href: `/explorer?type=${encodeURIComponent(entityKey)}`,
          icon: getEntityIcon(entityKey, entity.icon),
          description: entity.description || `Browse ${getEntityDisplayName(entityKey, true)}`,
        });
      }
    }

    // Generic explorer — always present alongside the entity-type shortcuts
    // (and the only knowledge_base item before domain config loads).
    knowledgeBaseItems.push({
      name: getNavLabel("knowledge_base", "/explorer", "Knowledge Explorer"),
      href: "/explorer",
      icon: Search,
      description: "Browse and search captured knowledge",
    });

    const allGroups = [
      {
        id: "intelligence",
        name: getGroupLabel("intelligence", "Intelligence"),
        defaultOpen: true,
        items: [
          {
            name: getNavLabel("intelligence", "/decisions", "Constitution"),
            href: "/decisions",
            icon: Scale,
            description: "Decision lifecycle, lineage, and constitution",
            badge: reviewCount ?? undefined,
          },
          {
            name: getNavLabel("intelligence", "/domain-graph-enhanced", "Graph"),
            href: "/domain-graph-enhanced",
            icon: Share2,
            description: "Interactive visualization of domain relationships",
          },
          {
            name: getNavLabel("intelligence", "/feed", "Activity"),
            href: "/feed",
            icon: Zap,
            description: "Live feed of decisions, action items, and insights",
          },
          {
            name: getNavLabel("intelligence", "/memory", "Memory"),
            href: "/memory",
            icon: Brain,
            description: "Captured thoughts and the memory review ladder",
          },
          {
            name: getNavLabel("intelligence", "/chat", "Ask"),
            href: "/chat",
            icon: MessageSquare,
            description: "Context-aware imi chat interface",
          },
        ],
      },
      {
        id: "knowledge_base",
        name: getGroupLabel("knowledge_base", "Knowledge Base"),
        defaultOpen: true,
        items: knowledgeBaseItems,
      },
      {
        id: "account",
        name: getGroupLabel("account", "Account"),
        defaultOpen: true,
        items: [
          {
            name: getNavLabel("account", "/profile", "Profile"),
            href: "/profile",
            icon: User,
            description: "User profile and preferences",
            requiresAuth: true,
          },
          {
            name: getNavLabel("account", "/command", "System"),
            href: "/command",
            icon: Settings,
            description: "System settings and command interface",
            requiresAuth: true,
          },
        ],
      },
    ];

    return allGroups;
  }, [getNavLabel, getGroupLabel, domainConfig, getEntityDisplayName, reviewCount]);
}

interface NavItem {
  name: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  description: string;
  requiresAuth?: boolean;
  badge?: number;
}

interface NavGroupProps {
  name: string;
  items: NavItem[];
  routePath: string | null;
  currentSearchParams: ReturnType<typeof useSearchParams>;
  user: unknown;
  defaultOpen?: boolean;
}

// Shared active-item class string — used by both NavGroup items and the
// ungrouped Overview link so they never drift apart.
const NAV_ITEM_ACTIVE =
  "bg-sidebar-active text-sidebar-active-text font-medium border-l-2 border-primary";
const NAV_ITEM_INACTIVE =
  "text-muted-foreground hover:bg-sidebar-item-hover hover:text-foreground";

function NavGroup({ name, items, routePath, currentSearchParams, user, defaultOpen = true }: NavGroupProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const filteredItems = items.filter((item) => !item.requiresAuth || user);

  if (filteredItems.length === 0) return null;

  return (
    <Collapsible.Root open={isOpen} onOpenChange={setIsOpen} className="mb-1">
      <Collapsible.Trigger asChild>
        <button
          className="flex w-full items-center justify-between px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-sidebar-group hover:text-foreground transition-colors"
          aria-label={`Toggle ${name} section`}
        >
          <span>{name}</span>
          <ChevronDown
            className={`h-3 w-3 transition-transform duration-200 ${
              isOpen ? "" : "-rotate-90"
            }`}
          />
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content className="overflow-hidden data-[state=open]:animate-accordion-down data-[state=closed]:animate-accordion-up">
        <div className="space-y-0.5 pb-2">
          {filteredItems.map((item) => {
            const currentRoute = (routePath || '').replace(/\/$/, '') || '/';
            const [itemPath, itemQueryString] = item.href.split('?');
            const itemRoute = itemPath.replace(/\/$/, '') || '/';

            let isActive: boolean;
            if (itemQueryString) {
              // Match pathname + all query params (e.g., /explorer?type=account)
              const itemParams = new URLSearchParams(itemQueryString);
              isActive = currentRoute === itemRoute &&
                Array.from(itemParams.entries()).every(([k, v]) => currentSearchParams?.get(k) === v);
            } else if (itemRoute === '/explorer') {
              // Generic Knowledge Explorer: NOT active when ?type= is present
              isActive = currentRoute === itemRoute && !currentSearchParams?.get('type');
            } else {
              isActive = currentRoute === itemRoute;
            }

            const Icon = item.icon;
            return (
              <BasePathLink
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 px-3 py-1.5 mx-2 rounded-md text-sm transition-all duration-150 ${
                  isActive ? NAV_ITEM_ACTIVE : NAV_ITEM_INACTIVE
                }`}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon className="h-4 w-4 flex-shrink-0" />
                <span className="flex-1">{item.name}</span>
                {item.badge ? (
                  <Badge variant="secondary" className="ml-auto">
                    {item.badge}
                  </Badge>
                ) : null}
              </BasePathLink>
            );
          })}
        </div>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}

function SidebarContentInner() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const basePath = useDetectedBasePath();
  const { user } = useAuth();
  const { uiLabels, getNavLabel } = useDomain();
  const { count: reviewCount } = useReviewCounts();
  const navGroups = useNavGroups(reviewCount);

  // Extract the route portion of the pathname (remove basePath if present)
  const routePath = basePath && pathname?.startsWith(basePath)
    ? pathname.slice(basePath.length)
    : pathname;

  const appName = uiLabels?.app_name ?? "imi";

  // Determine if Overview item is active
  const overviewRoute = "/overview";
  const currentRoute = (routePath || '').replace(/\/$/, '') || '/';
  const isOverviewActive = currentRoute === overviewRoute;

  return (
    <div className="flex h-full flex-col bg-sidebar">
      {/* Header — fixed h-14 aligns bottom border with TopBar */}
      <div className="flex h-14 items-center gap-3 px-4 border-b border-sidebar">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Sparkles className="h-4 w-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-foreground text-sm truncate">{appName}</div>
          {user && (
            <div className="text-xs text-muted-foreground truncate">
              {user.firstName && user.lastName
                ? `${user.firstName} ${user.lastName}`
                : user.email}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1">
          <LoginButton />
          <UserMenu />
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        {/* Overview — ungrouped home item rendered above groups */}
        <BasePathLink
          href={overviewRoute}
          className={`flex items-center gap-3 px-3 py-1.5 mx-2 mb-2 rounded-md text-sm transition-all duration-150 ${
            isOverviewActive ? NAV_ITEM_ACTIVE : NAV_ITEM_INACTIVE
          }`}
          aria-current={isOverviewActive ? "page" : undefined}
        >
          <Home className="h-4 w-4 flex-shrink-0" />
          <span className="flex-1">{getNavLabel("intelligence", "/overview", "Overview")}</span>
        </BasePathLink>

        {navGroups.map((group) => (
          <NavGroup
            key={group.id}
            name={group.name}
            items={group.items}
            routePath={routePath}
            currentSearchParams={searchParams}
            user={user}
            defaultOpen={group.defaultOpen}
          />
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-sidebar p-3 space-y-1">
        <div className="px-3 py-2 text-xs text-muted-foreground">
          {appName}
        </div>
      </div>
    </div>
  );
}

function SidebarContent() {
  return (
    <Suspense>
      <SidebarContentInner />
    </Suspense>
  );
}

/**
 * Mobile hamburger trigger + Sheet overlay.
 * Intended to be rendered inside a top bar (e.g. TopBar) — the trigger has no
 * fixed positioning; callers control placement.
 */
export function MobileNav() {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Open navigation menu"
        >
          <Menu className="h-5 w-5" />
          <span className="sr-only">Open navigation menu</span>
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-64 p-0 bg-sidebar">
        <SidebarContent />
      </SheetContent>
    </Sheet>
  );
}

/**
 * Desktop sidebar — hidden on mobile, visible md+.
 */
export default function Navigation() {
  return (
    <div className="hidden md:flex w-64 border-r border-sidebar h-full flex-col">
      <SidebarContent />
    </div>
  );
}
