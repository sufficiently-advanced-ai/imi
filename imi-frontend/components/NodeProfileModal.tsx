'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  MoreHorizontal,
  Filter,
  EyeOff,
  Maximize2,
  Target,
  User,
  Briefcase,
  Users,
  Building2,
  FileText,
  Activity,
  TrendingUp,
  Calendar,
  BookOpen
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useDomain } from '@/contexts/DomainContext';

// Types for the entity profile API response
interface EntityStatistics {
  total_mentions: number;
  recent_mentions: number;
  document_count: number;
  activity_count: number;
  relationship_count: number;
  last_activity: string | null;
  quality_score: number;
  completeness_score: number;
}

interface EntityActivity {
  entity_id: string;
  activity_type: string;
  activity_date: string;
  description: string;
  document_path: string;
  relevance_score: number;
  metadata: Record<string, unknown>;
}

interface EntityRelationship {
  entity_id: string;
  relationship_type: string;
  strength: number;
  last_interaction: string | null;
  interaction_count: number;
}

// Entity data from API can have various fields depending on type
interface EntityData {
  id?: string;
  entity_type?: string;
  type?: string;
  canonical_name?: string;
  aliases?: string[];
  confidence_score?: number;
  // Person fields
  titles?: string[];
  role?: string;
  title?: string;
  departments?: string[];
  department?: string;
  email?: string;
  // Project fields
  status?: string;
  teams?: string[];
  start_date?: string;
  // Team fields
  division?: string;
  members?: string[];
  lead?: string;
  // Organization fields
  organization_type?: string;
  account_type?: string;
  industry?: string;
  location?: string;
  [key: string]: unknown; // Allow additional fields
}

interface EntityProfileData {
  entity: EntityData;
  statistics: EntityStatistics;
  recent_activity: EntityActivity[];
  top_relationships: EntityRelationship[];
  insights: Array<{
    insight_type: string;
    content: string;
    confidence: number;
  }>;
  narrative_profile: string | null;
}

interface GraphNodeData {
  id: string;
  entityType?: string;
  entity_type?: string;
  degree?: number;
  attributes?: {
    name?: string;
    canonical_name?: string;
    department?: string;
    documents?: string[];
    [key: string]: unknown;
  };
  metadata?: {
    documents?: string[];
    connection_count?: number;
    [key: string]: unknown;
  };
  metrics?: {
    degree_centrality?: number;
    betweenness_centrality?: number;
    closeness_centrality?: number;
  };
}

interface NodeProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  nodeData: GraphNodeData | null;
  onAction?: (action: string, nodeId: string) => void;
}

// Entity type icon mapping
const getEntityIcon = (entityType: string) => {
  switch (entityType?.toLowerCase()) {
    case 'person':
    case 'member':
      return <User className="h-5 w-5" />;
    case 'project':
      return <Briefcase className="h-5 w-5" />;
    case 'team':
    case 'cohort':
      return <Users className="h-5 w-5" />;
    case 'organization':
    case 'account':
      return <Building2 className="h-5 w-5" />;
    default:
      return <FileText className="h-5 w-5" />;
  }
};

// Entity type color mapping
const getEntityColor = (entityType: string): string => {
  switch (entityType?.toLowerCase()) {
    case 'person':
    case 'member':
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    case 'project':
      return 'bg-green-500/20 text-green-400 border-green-500/30';
    case 'team':
    case 'cohort':
      return 'bg-purple-500/20 text-purple-400 border-purple-500/30';
    case 'organization':
    case 'account':
      return 'bg-orange-500/20 text-orange-400 border-orange-500/30';
    default:
      return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  }
};

// Format date for display
const formatDate = (dateString: string | null): string => {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  });
};

// Format relative time
const formatRelativeTime = (dateString: string | null): string => {
  if (!dateString) return 'No recent activity';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
  return `${Math.floor(diffDays / 365)} years ago`;
};

// Activity type badge color
const getActivityColor = (activityType: string): string => {
  switch (activityType?.toLowerCase()) {
    case 'meeting':
      return 'bg-blue-500/10 text-blue-400';
    case 'commit':
      return 'bg-green-500/10 text-green-400';
    case 'document':
      return 'bg-purple-500/10 text-purple-400';
    case 'mention':
      return 'bg-yellow-500/10 text-yellow-400';
    default:
      return 'bg-gray-500/10 text-gray-400';
  }
};

export const NodeProfileModal: React.FC<NodeProfileModalProps> = ({
  isOpen,
  onClose,
  nodeData,
  onAction
}) => {
  const [profileData, setProfileData] = useState<EntityProfileData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('overview');
  // Domain schema drives the structured "Entity Details" fields (labels, order,
  // types) so every domain's entities render their real frontmatter, not a
  // hardcoded per-type subset.
  const { domainConfig } = useDomain();

  // Fetch entity profile from API with abort support
  const fetchEntityProfile = useCallback(async (entityId: string, signal?: AbortSignal) => {
    setIsLoading(true);
    setError(null);

    try {
      const profileUrl = `/api/entities/${entityId}/profile?include_activity=true&include_relationships=true`;

      const response = await fetch(profileUrl, {
        headers: {
          'X-Domain-ID': localStorage.getItem('currentDomain') || 'default'
        },
        signal
      });

      if (!response.ok) {
        if (response.status === 404) {
          // Entity not found in registry - use graph node data as fallback
          setProfileData(null);
          return;
        }
        throw new Error(`Failed to fetch profile: ${response.statusText}`);
      }

      const data = await response.json();
      setProfileData(data);
    } catch (err) {
      // Ignore abort errors - these are expected when modal closes
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      setError(err instanceof Error ? err.message : 'Failed to load profile');
      setProfileData(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen && nodeData?.id) {
      // Clear previous profile data when modal opens or nodeData changes.
      // Land on Overview — it now carries the structured schema fields and the
      // narrative body together, so it's the richest single view.
      setProfileData(null);
      setError(null);
      setActiveTab('overview');

      // Create abort controller for this fetch
      const abortController = new AbortController();
      fetchEntityProfile(nodeData.id, abortController.signal);

      // Cleanup: abort fetch when modal closes or nodeData changes
      return () => {
        abortController.abort();
      };
    }
  }, [isOpen, nodeData?.id, fetchEntityProfile]);

  const handleAction = (action: string) => {
    if (onAction && nodeData) {
      onAction(action, nodeData.id);
      onClose();
    }
  };

  if (!nodeData) return null;

  // Determine display data - prefer profile API data over graph node data
  const entityType = String(
    profileData?.entity?.entity_type ||
    profileData?.entity?.type ||
    nodeData?.entityType ||
    nodeData?.entity_type ||
    'unknown'
  );
  const entityName = String(
    profileData?.entity?.canonical_name ||
    nodeData?.attributes?.name ||
    nodeData?.id ||
    'Unknown Entity'
  );
  const statistics = profileData?.statistics;
  const activities = profileData?.recent_activity || [];
  const relationships = profileData?.top_relationships || [];

  // Entity-specific attributes
  const getEntitySpecificInfo = () => {
    const entity: EntityData = profileData?.entity || (nodeData?.attributes as EntityData) || {};
    const type = String(entityType).toLowerCase();

    // Schema-driven path: render every attribute the active domain defines for
    // this entity type, with proper labels/order/formatting. The graph node's
    // `attributes` always carry the frontmatter, so we read from there first and
    // fall back to the profile API's entity dict.
    const schema = domainConfig?.entities?.[type] ?? domainConfig?.entities?.[entityType];
    const schemaAttrs = (schema?.attributes ?? []) as Array<{
      name: string;
      label?: string;
      type?: string;
      unit?: string | null;
    }>;
    if (schemaAttrs.length > 0) {
      const humanize = (s: string) =>
        s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
      const formatValue = (attr: { type?: string; unit?: string | null }, raw: unknown): string => {
        if (attr.type === 'date') return formatDate(String(raw));
        if (attr.type === 'number') return attr.unit ? `${raw} ${attr.unit}` : String(raw);
        if (attr.type === 'enum') return humanize(String(raw));
        return String(raw);
      };
      const attrs = nodeData?.attributes as Record<string, unknown> | undefined;
      const items = schemaAttrs
        .filter((attr) => attr.name !== 'name') // shown as the dialog title
        .map((attr) => {
          const raw = attrs?.[attr.name] ?? (entity as Record<string, unknown>)?.[attr.name];
          return { attr, raw };
        })
        .filter(({ raw }) =>
          raw !== undefined &&
          raw !== null &&
          raw !== '' &&
          // Keep non-empty arrays (e.g. titles, members); drop plain objects.
          (Array.isArray(raw) ? raw.length > 0 : typeof raw !== 'object')
        )
        .map(({ attr, raw }) => ({
          label: attr.label || humanize(attr.name),
          value: Array.isArray(raw)
            ? raw.map((v) => formatValue(attr, v)).join(', ')
            : formatValue(attr, raw),
        }));
      return { items };
    }

    switch (type) {
      case 'person':
        return {
          items: [
            { label: 'Role', value: entity.titles?.[0] || entity.role || entity.title },
            { label: 'Department', value: entity.departments?.[0] || entity.department },
            { label: 'Email', value: entity.email },
          ].filter(item => item.value)
        };
      case 'project':
        return {
          items: [
            { label: 'Status', value: entity.status },
            { label: 'Teams', value: entity.teams?.join(', ') },
            { label: 'Start Date', value: formatDate(entity.start_date || null) },
          ].filter(item => item.value && item.value !== 'N/A')
        };
      case 'team':
        return {
          items: [
            { label: 'Department', value: entity.department },
            { label: 'Division', value: entity.division },
            { label: 'Members', value: entity.members?.length ? `${entity.members.length} members` : undefined },
            { label: 'Lead', value: entity.lead },
          ].filter(item => item.value)
        };
      case 'organization':
      case 'account':
        return {
          items: [
            { label: 'Type', value: entity.organization_type || entity.account_type },
            { label: 'Industry', value: entity.industry },
            { label: 'Location', value: entity.location },
          ].filter(item => item.value)
        };
      case 'member':
        return {
          items: [
            { label: 'Role', value: entity.role || entity.title },
            { label: 'Organization', value: typeof entity.organization === 'string' ? entity.organization : undefined },
            { label: 'Geography', value: typeof entity.geography === 'string' ? entity.geography : undefined },
            { label: 'Cohort', value: typeof entity.cohort === 'string' ? entity.cohort : undefined },
            { label: 'Sector', value: typeof entity.sector === 'string' ? entity.sector : undefined },
          ].filter(item => item.value)
        };
      default:
        // Show all available attributes for unknown types
        return {
          items: Object.entries(entity)
            .filter(([key, value]) =>
              value &&
              !['id', 'entity_type', 'type', 'canonical_name', 'aliases', 'confidence_score'].includes(key) &&
              typeof value !== 'object'
            )
            .slice(0, 5)
            .map(([key, value]) => ({
              label: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
              value: String(value)
            }))
        };
    }
  };

  const entityInfo = getEntitySpecificInfo();

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader className="flex-shrink-0">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <div className={`p-2 rounded-lg ${getEntityColor(entityType)}`}>
                {getEntityIcon(entityType)}
              </div>
              <div className="min-w-0">
                <DialogTitle className="text-xl truncate">{entityName}</DialogTitle>
                <Badge variant="outline" className="mt-1 capitalize">
                  {entityType}
                </Badge>
              </div>
            </div>

            {/* Actions Menu */}
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="ghost" size="icon" className="flex-shrink-0">
                  <MoreHorizontal className="h-5 w-5" />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-48 p-1">
                <div className="flex flex-col">
                  <button
                    onClick={() => handleAction('focus_node')}
                    className="flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent transition-colors text-left"
                  >
                    <Target className="h-4 w-4" />
                    Focus on Node
                  </button>
                  <button
                    onClick={() => handleAction('filter_by_node')}
                    className="flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent transition-colors text-left"
                  >
                    <Filter className="h-4 w-4" />
                    Filter by Node
                  </button>
                  <button
                    onClick={() => handleAction('expand_connections')}
                    className="flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent transition-colors text-left"
                  >
                    <Maximize2 className="h-4 w-4" />
                    Expand Connections
                  </button>
                  <button
                    onClick={() => handleAction('hide_node')}
                    className="flex items-center gap-2 px-3 py-2 text-sm rounded-md hover:bg-accent transition-colors text-left"
                  >
                    <EyeOff className="h-4 w-4" />
                    Hide Node
                  </button>
                </div>
              </PopoverContent>
            </Popover>
          </div>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 overflow-hidden flex flex-col mt-4">
          <TabsList className="grid w-full grid-cols-3 flex-shrink-0">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="profile">
              <BookOpen className="h-3.5 w-3.5 mr-1.5" />
              Profile
            </TabsTrigger>
            <TabsTrigger value="connections">
              Connections {relationships.length > 0 && `(${relationships.length})`}
            </TabsTrigger>
          </TabsList>

          {/* Overview Tab */}
          <TabsContent value="overview" className="flex-1 overflow-y-auto mt-4 space-y-4">
            {isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-32 w-full" />
                <Skeleton className="h-48 w-full" />
              </div>
            ) : error ? (
              <Card className="border-destructive/50">
                <CardContent className="py-6 text-center">
                  <p className="text-destructive mb-2">{error}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fetchEntityProfile(nodeData.id)}
                  >
                    Retry
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <>
                {/* Statistics Card */}
                {statistics && (
                  <Card>
                    <CardHeader className="py-3">
                      <CardTitle className="text-sm font-medium flex items-center gap-2">
                        <TrendingUp className="h-4 w-4" />
                        Statistics
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="py-2">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="text-center">
                          <div className="text-2xl font-bold text-primary">
                            {statistics.total_mentions}
                          </div>
                          <div className="text-xs text-muted-foreground">Total Mentions</div>
                        </div>
                        <div className="text-center">
                          <div className="text-2xl font-bold text-primary">
                            {statistics.document_count}
                          </div>
                          <div className="text-xs text-muted-foreground">Documents</div>
                        </div>
                        <div className="text-center">
                          <div className="text-2xl font-bold text-primary">
                            {statistics.relationship_count}
                          </div>
                          <div className="text-xs text-muted-foreground">Relationships</div>
                        </div>
                        <div className="text-center">
                          <div className="text-2xl font-bold text-primary">
                            {statistics.activity_count}
                          </div>
                          <div className="text-xs text-muted-foreground">Activities</div>
                        </div>
                      </div>
                      {statistics.last_activity && (
                        <div className="mt-3 pt-3 border-t text-center text-sm text-muted-foreground">
                          Last active: {formatRelativeTime(statistics.last_activity)}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                {/* Entity Details Card */}
                {entityInfo.items.length > 0 && (
                  <Card>
                    <CardHeader className="py-3">
                      <CardTitle className="text-sm font-medium flex items-center gap-2">
                        {getEntityIcon(entityType)}
                        {entityType.charAt(0).toUpperCase() + entityType.slice(1)} Details
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="py-2">
                      <div className="space-y-2">
                        {entityInfo.items.map((item, index) => (
                          <div key={index} className="flex justify-between items-center py-1">
                            <span className="text-sm text-muted-foreground">{item.label}</span>
                            <span className="text-sm font-medium">{item.value}</span>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* Profile narrative — the unstructured body, shown alongside the
                    structured fields so both are visible without switching tabs. */}
                {profileData?.narrative_profile && (
                  <Card>
                    <CardHeader className="py-3">
                      <CardTitle className="text-sm font-medium flex items-center gap-2">
                        <BookOpen className="h-4 w-4" />
                        Profile
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="py-2">
                      <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:text-foreground prose-p:text-muted-foreground prose-li:text-muted-foreground prose-strong:text-foreground">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {profileData.narrative_profile}
                        </ReactMarkdown>
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* Activity Timeline Card */}
                {activities.length > 0 && (
                  <Card>
                    <CardHeader className="py-3">
                      <CardTitle className="text-sm font-medium flex items-center gap-2">
                        <Activity className="h-4 w-4" />
                        Recent Activity
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="py-2">
                      <div className="space-y-3">
                        {activities.slice(0, 5).map((activity, index) => (
                          <div key={index} className="flex items-start gap-3">
                            <div className="flex-shrink-0 mt-1">
                              <div className="w-2 h-2 rounded-full bg-primary" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-1">
                                <Badge
                                  variant="outline"
                                  className={`text-xs ${getActivityColor(activity.activity_type)}`}
                                >
                                  {activity.activity_type}
                                </Badge>
                                <span className="text-xs text-muted-foreground">
                                  {formatRelativeTime(activity.activity_date)}
                                </span>
                              </div>
                              <p className="text-sm text-foreground line-clamp-2">
                                {activity.description}
                              </p>
                              {/* TODO: Implement document navigation - hiding until ready */}
                            </div>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* Graph-derived stats when no profile data */}
                {!statistics && !profileData && nodeData && (
                  <Card>
                    <CardHeader className="py-3">
                      <CardTitle className="text-sm font-medium flex items-center gap-2">
                        <TrendingUp className="h-4 w-4" />
                        Graph Information
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="py-2">
                      <div className="grid grid-cols-2 gap-4">
                        <div className="text-center">
                          <div className="text-2xl font-bold text-primary">
                            {nodeData.metadata?.connection_count || nodeData.degree || 0}
                          </div>
                          <div className="text-xs text-muted-foreground">Connections</div>
                        </div>
                        <div className="text-center">
                          <div className="text-2xl font-bold text-primary">
                            {nodeData.metadata?.documents?.length || 0}
                          </div>
                          <div className="text-xs text-muted-foreground">Documents</div>
                        </div>
                      </div>
                      <div className="mt-4 pt-3 border-t text-center">
                        <p className="text-xs text-muted-foreground">
                          Entity exists in graph but is not yet registered.
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">
                          Full profile data (activity, statistics) will be available once registered.
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                )}
              </>
            )}
          </TabsContent>

          {/* Profile Tab - Full Narrative Content */}
          <TabsContent value="profile" className="flex-1 overflow-y-auto mt-4">
            {isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-8 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
                <Skeleton className="h-8 w-1/2 mt-6" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-4/5" />
              </div>
            ) : profileData?.narrative_profile ? (
              <Card>
                <CardContent className="py-4">
                  <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:text-foreground prose-p:text-muted-foreground prose-li:text-muted-foreground prose-strong:text-foreground">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {profileData.narrative_profile}
                    </ReactMarkdown>
                  </div>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="py-8 text-center">
                  <BookOpen className="h-12 w-12 mx-auto text-muted-foreground/50 mb-3" />
                  <p className="text-muted-foreground">
                    No narrative profile available for this entity.
                  </p>
                  <p className="text-sm text-muted-foreground mt-2">
                    Profile narratives are generated from entity documentation files.
                  </p>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* Connections Tab */}
          <TabsContent value="connections" className="flex-1 overflow-y-auto mt-4">
            {isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3].map(i => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : relationships.length > 0 ? (
              <div className="space-y-2">
                {relationships.map((rel, index) => (
                  <Card
                    key={index}
                    className="cursor-pointer hover:bg-accent/50 transition-colors"
                    onClick={() => {
                      if (onAction) {
                        onAction('navigate_to_node', rel.entity_id);
                      }
                    }}
                  >
                    <CardContent className="py-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                            <User className="h-4 w-4 text-primary" />
                          </div>
                          <div>
                            <p className="font-medium text-sm">{rel.entity_id}</p>
                            <p className="text-xs text-muted-foreground capitalize">
                              {rel.relationship_type.replace(/_/g, ' ')}
                            </p>
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Calendar className="h-3 w-3" />
                            {formatRelativeTime(rel.last_interaction)}
                          </div>
                          <div className="mt-1">
                            {(() => {
                              // Clamp strength to 0-1 range to avoid overflow
                              const boundedStrength = Math.max(0, Math.min(1, rel.strength));
                              return (
                                <>
                                  <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                                    <div
                                      className="h-full bg-primary rounded-full"
                                      style={{ width: `${boundedStrength * 100}%` }}
                                    />
                                  </div>
                                  <span className="text-xs text-muted-foreground">
                                    {Math.round(boundedStrength * 100)}% strength
                                  </span>
                                </>
                              );
                            })()}
                          </div>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : (
              <Card>
                <CardContent className="py-8 text-center">
                  <Users className="h-12 w-12 mx-auto text-muted-foreground/50 mb-3" />
                  <p className="text-muted-foreground">
                    No connections found for this entity.
                  </p>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
};
