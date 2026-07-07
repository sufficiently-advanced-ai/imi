'use client';

import React, { createContext, useContext, useState, useEffect, ReactNode, useRef, useCallback } from 'react';
import { getApiUrl } from '@/lib/config';

interface DomainConfig {
  id: string;
  name: string;
  entities: Record<string, any>;
  relationships: Record<string, any>;
  ui?: UILabels;
}

interface DomainInfo {
  id: string;
  name: string;
  description?: string;
  active?: boolean;
}

interface UINavItem {
  label: string;
  description?: string;
}

interface UINavGroup {
  label?: string;
  items: Record<string, UINavItem>;
}

interface UILabels {
  app_name: string;
  entity_label: string;
  graph_label: string;
  nav_groups: Record<string, UINavGroup>;
  terminology: Record<string, string>;
}

interface DomainContextType {
  currentDomain: string | null;
  domains: string[];
  domainInfos: DomainInfo[];
  domainConfig: DomainConfig | null;
  isLoading: boolean;
  error: string | null;
  setCurrentDomain: (domain: string) => Promise<void>;
  uiLabels: UILabels | null;
  getNavLabel: (groupId: string, path: string, fallback: string) => string;
  getGroupLabel: (groupId: string, fallback: string) => string;
  getTerm: (key: string, fallback: string) => string;
  getEntityDisplayName: (entityKey: string, plural?: boolean) => string;
}

const DomainContext = createContext<DomainContextType | undefined>(undefined);

export const useDomain = () => {
  const context = useContext(DomainContext);
  if (!context) {
    throw new Error('useDomain must be used within a DomainProvider');
  }
  return context;
};

interface DomainProviderProps {
  children: ReactNode;
}

export const DomainProvider: React.FC<DomainProviderProps> = ({ children }) => {
  const [currentDomain, setCurrentDomainState] = useState<string | null>(null);
  const [domains, setDomains] = useState<string[]>([]);
  const [domainInfos, setDomainInfos] = useState<DomainInfo[]>([]);
  const [domainConfig, setDomainConfig] = useState<DomainConfig | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Load available domains on mount
  useEffect(() => {
    loadDomains();
  }, []);

  // Load domain config when domain changes
  useEffect(() => {
    if (currentDomain) {
      loadDomainConfig(currentDomain);
    }
  }, [currentDomain]);

  const loadDomains = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await fetch(getApiUrl('/domain/domains'), {
        credentials: 'include' as RequestCredentials,
      });
      if (!response.ok) {
        throw new Error('Failed to load domains');
      }
      const data = await response.json();
      // API returns array directly, not wrapped in {domains: [...]}
      const domainList: DomainInfo[] = Array.isArray(data) ? data : (data.domains || []);
      setDomainInfos(domainList);
      setDomains(domainList.map((d: DomainInfo) => d.id));

      // Set default domain: use the active domain from the backend
      if (domainList.length > 0 && !currentDomain) {
        const activeDomain = domainList.find((d: DomainInfo) => d.active);
        const defaultDomain = activeDomain ? activeDomain.id : domainList[0].id;
        setCurrentDomainState(defaultDomain);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load domains');
      console.error('Error loading domains:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const loadDomainConfig = async (_domain: string) => {
    // Cancel any pending request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    try {
      setIsLoading(true);
      setError(null);

      // Fetch the active domain config
      const configResponse = await fetch(getApiUrl('/domain/config'), {
        signal: abortControllerRef.current.signal,
        credentials: 'include' as RequestCredentials,
      });

      if (!configResponse.ok) {
        throw new Error('Failed to load domain configuration');
      }

      const config = await configResponse.json();
      setDomainConfig(config);
    } catch (err) {
      // Ignore abort errors
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      setError(err instanceof Error ? err.message : 'Failed to load domain configuration');
      console.error('Error loading domain config:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const setCurrentDomain = async (domain: string) => {
    setCurrentDomainState(domain);
    await loadDomainConfig(domain);
  };

  // Derived UI labels from domain config
  const uiLabels: UILabels | null = domainConfig?.ui ?? null;

  // Helper: get a nav item label with fallback
  const getNavLabel = useCallback((groupId: string, path: string, fallback: string): string => {
    return uiLabels?.nav_groups?.[groupId]?.items?.[path]?.label ?? fallback;
  }, [uiLabels]);

  // Helper: get a nav group label with fallback
  const getGroupLabel = useCallback((groupId: string, fallback: string): string => {
    return uiLabels?.nav_groups?.[groupId]?.label ?? fallback;
  }, [uiLabels]);

  // Helper: get a terminology override with fallback
  const getTerm = useCallback((key: string, fallback: string): string => {
    return uiLabels?.terminology?.[key] ?? fallback;
  }, [uiLabels]);

  // Helper: get display name for an entity type key
  const getEntityDisplayName = useCallback((entityKey: string, plural: boolean = false): string => {
    const entity = domainConfig?.entities?.[entityKey];
    if (!entity) {
      // Fallback: capitalize and replace underscores
      const name = entityKey.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      return plural ? name + 's' : name;
    }
    if (plural) {
      return entity.plural_label ?? entity.plural ?? entityKey;
    }
    return entity.label ?? entity.name ?? entityKey;
  }, [domainConfig]);

  const value: DomainContextType = {
    currentDomain,
    domains,
    domainInfos,
    domainConfig,
    isLoading,
    error,
    setCurrentDomain,
    uiLabels,
    getNavLabel,
    getGroupLabel,
    getTerm,
    getEntityDisplayName,
  };

  return <DomainContext.Provider value={value}>{children}</DomainContext.Provider>;
};
