// Graph & signal mutation API client — companion to domain.ts (read-only).
// Backed by the routes added in PR #878: entity CRUD (existing), signal
// mutations (new), and the type registry (new).

import { getApiUrl } from '@/lib/config';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

type MutationEnvelope = { success?: boolean; error?: string };

// Many of these endpoints return 200 with an in-body { success: false, error }
// envelope rather than an HTTP error. Promote that to a thrown error so callers
// don't treat a logical failure as success.
function assertMutationSuccess<T>(data: T): T {
  if (
    data &&
    typeof data === 'object' &&
    'success' in (data as Record<string, unknown>) &&
    (data as MutationEnvelope).success === false
  ) {
    throw new Error((data as MutationEnvelope).error || 'Request failed');
  }
  return data;
}

async function jsonFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(getApiUrl(path), {
    credentials: 'include' as RequestCredentials,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
    ...init,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body?.detail || body?.error || detail;
    } catch {
      // leave detail as statusText
    }
    throw new Error(`[${response.status}] ${detail}`);
  }

  if (response.status === 204) return undefined as unknown as T;
  return assertMutationSuccess((await response.json()) as T);
}

// ---------------------------------------------------------------------------
// Node / entity CRUD
// ---------------------------------------------------------------------------

export interface CreateNodeInput {
  entity_type: string;
  attributes: Record<string, unknown>;
  relationships?: Record<string, unknown>;
}

export interface UpdateNodeInput {
  attributes?: Record<string, unknown>;
  relationships?: Record<string, unknown>;
}

export interface EntityMutationResult {
  success: boolean;
  entity?: { id: string; entity_type?: string; attributes?: Record<string, unknown> };
  error?: string;
}

export function createNode(input: CreateNodeInput) {
  return jsonFetch<EntityMutationResult>('/entities/create', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function updateNode(entityId: string, input: UpdateNodeInput) {
  return jsonFetch<EntityMutationResult>(`/entities/${encodeURIComponent(entityId)}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function deleteNode(entityId: string) {
  // Deleting a node from the graph means "remove this node and detach its
  // edges". Neo4j refuses a plain DELETE on a node that still has
  // relationships, so we ask the backend to cascade (DETACH DELETE).
  // Without this, any connected node fails with a "Use cascade=True" 400.
  return jsonFetch<EntityMutationResult>(`/entities/${encodeURIComponent(entityId)}`, {
    method: 'DELETE',
    body: JSON.stringify({ handle_relationships: 'cascade' }),
  });
}

// ---------------------------------------------------------------------------
// Merge (manual select-two)
// ---------------------------------------------------------------------------

export interface MergeImpact {
  aliases_to_merge?: string[];
  relationships_affected?: number;
  data_conflicts?: Array<{ field: string; duplicate_value: unknown; primary_value: unknown }>;
}

export interface MergeNodesResult {
  success: boolean;
  preview?: boolean;
  merged_entity_id?: string;
  merge_impact?: MergeImpact;
  merge_summary?: Record<string, unknown>;
  error?: string;
}

// Merge `duplicateId` into `primaryId` (the survivor). The endpoint is keyed
// on the primary; `primary_id` in the body makes the survivor explicit so the
// backend never has to guess. Pass preview=true for a non-mutating impact
// summary to show in the confirm dialog.
export function mergeNodes(
  primaryId: string,
  duplicateId: string,
  opts: { preview?: boolean } = {},
) {
  return jsonFetch<MergeNodesResult>(
    `/entities/${encodeURIComponent(primaryId)}/merge`,
    {
      method: 'POST',
      body: JSON.stringify({
        target_entity_id: duplicateId,
        primary_id: primaryId,
        preview: opts.preview ?? false,
      }),
    },
  );
}

// ---------------------------------------------------------------------------
// Relationship CRUD
// ---------------------------------------------------------------------------

export interface AddRelationshipInput {
  relationship_type: string;
  target_entity_id: string;
}

export function addRelationship(
  sourceEntityId: string,
  input: AddRelationshipInput,
) {
  return jsonFetch<EntityMutationResult>(
    `/entities/${encodeURIComponent(sourceEntityId)}/relationships`,
    { method: 'POST', body: JSON.stringify(input) },
  );
}

export function removeRelationship(
  sourceEntityId: string,
  relationshipType: string,
  targetEntityId: string,
) {
  return jsonFetch<EntityMutationResult>(
    `/entities/${encodeURIComponent(sourceEntityId)}/relationships/` +
      `${encodeURIComponent(relationshipType)}/${encodeURIComponent(targetEntityId)}`,
    { method: 'DELETE' },
  );
}

// ---------------------------------------------------------------------------
// Profile editing (body quick-edit + durable corrections overlay)
// ---------------------------------------------------------------------------

export interface ProfileEditable {
  entity_id: string;
  body: string;
  manual_corrections: string[];
}

export interface ProfileMutationResult {
  success: boolean;
  entity_id?: string;
  body?: string;
  manual_corrections?: string[];
  error?: string;
}

// Load the editable narrative body + durable corrections for an entity.
export function getProfileEditable(entityId: string) {
  return jsonFetch<ProfileEditable>(
    `/entities/${encodeURIComponent(entityId)}/profile/body`,
  );
}

// Replace the narrative body. Immediate but NOT regeneration-safe: the AI
// enricher may re-derive the body on the next meeting. For permanent fixes
// callers should also add a correction (below).
export function updateProfileBody(entityId: string, body: string) {
  return jsonFetch<ProfileMutationResult>(
    `/entities/${encodeURIComponent(entityId)}/profile`,
    { method: 'PUT', body: JSON.stringify({ body }) },
  );
}

// Replace the durable corrections list. Stored in frontmatter and injected
// into the authoritative grounding block, so corrections survive both a graph
// rebuild and AI regeneration.
export function updateCorrections(entityId: string, corrections: string[]) {
  return jsonFetch<ProfileMutationResult>(
    `/entities/${encodeURIComponent(entityId)}/corrections`,
    { method: 'PUT', body: JSON.stringify({ manual_corrections: corrections }) },
  );
}

// ---------------------------------------------------------------------------
// Signal mutations (PR #878)
// ---------------------------------------------------------------------------

export interface SignalPayload {
  id: string;
  type: string;
  content: string;
  status?: string | null;
  [key: string]: unknown;
}

export interface SignalMutationResult {
  success: boolean;
  signal?: SignalPayload;
  neo4j_synced?: boolean;
  error?: string;
}

export interface BulkSignalResult {
  signal_id: string;
  success: boolean;
  error?: string | null;
}

export interface BulkSignalResponse {
  total: number;
  succeeded: number;
  failed: number;
  results: BulkSignalResult[];
}

export function closeSignal(signalId: string) {
  return jsonFetch<SignalMutationResult>(
    `/signals/${encodeURIComponent(signalId)}/close`,
    { method: 'POST' },
  );
}

export function reopenSignal(signalId: string) {
  return jsonFetch<SignalMutationResult>(
    `/signals/${encodeURIComponent(signalId)}/reopen`,
    { method: 'POST' },
  );
}

export function markSignalInProgress(signalId: string) {
  return jsonFetch<SignalMutationResult>(
    `/signals/${encodeURIComponent(signalId)}/in-progress`,
    { method: 'POST' },
  );
}

export function updateSignal(
  signalId: string,
  updates: { status?: string; content?: string; owner_id?: string; due_date?: string },
) {
  return jsonFetch<SignalMutationResult>(
    `/signals/${encodeURIComponent(signalId)}/update`,
    { method: 'POST', body: JSON.stringify(updates) },
  );
}

export function bulkCloseSignals(signalIds: string[]) {
  return jsonFetch<BulkSignalResponse>('/signals/bulk/close', {
    method: 'POST',
    body: JSON.stringify({ signal_ids: signalIds }),
  });
}

export function bulkSetSignalStatus(signalIds: string[], status: string) {
  return jsonFetch<BulkSignalResponse>('/signals/bulk/status', {
    method: 'POST',
    body: JSON.stringify({ signal_ids: signalIds, status }),
  });
}

// ---------------------------------------------------------------------------
// Type registry (PR #878)
// ---------------------------------------------------------------------------

export type TypeKind = 'entity' | 'relationship' | 'attribute';
export type TypeStatus = 'canonical' | 'provisional' | 'aliased' | 'deprecated';

export interface TypeEntry {
  name: string;
  kind: TypeKind;
  status: TypeStatus;
  domain_id: string;
  created_at: string;
  created_by: string;
  usage_count: number;
  aliased_to?: string | null;
  context?: Record<string, unknown>;
}

export interface TypeRegistryFilter {
  kind?: TypeKind;
  status?: TypeStatus;
  domainId?: string;
}

export function fetchTypeRegistry(filter: TypeRegistryFilter = {}) {
  const params = new URLSearchParams();
  if (filter.kind) params.set('kind', filter.kind);
  if (filter.status) params.set('status', filter.status);
  if (filter.domainId) params.set('domain_id', filter.domainId);
  const qs = params.toString() ? `?${params.toString()}` : '';
  return jsonFetch<TypeEntry[]>(`/type-registry${qs}`);
}

export interface PromoteResponse {
  entry: TypeEntry;
  yaml_snippet: string;
}

export function promoteType(kind: TypeKind, name: string) {
  return jsonFetch<PromoteResponse>(
    `/type-registry/${kind}/${encodeURIComponent(name)}/promote`,
    { method: 'POST' },
  );
}

export function aliasType(kind: TypeKind, name: string, target: string) {
  return jsonFetch<TypeEntry>(
    `/type-registry/${kind}/${encodeURIComponent(name)}/alias`,
    { method: 'POST', body: JSON.stringify({ target }) },
  );
}

export function deprecateType(kind: TypeKind, name: string) {
  return jsonFetch<TypeEntry>(
    `/type-registry/${kind}/${encodeURIComponent(name)}/deprecate`,
    { method: 'POST' },
  );
}

export function deleteType(kind: TypeKind, name: string) {
  return jsonFetch<{ deleted: boolean; name: string; kind: string }>(
    `/type-registry/${kind}/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
  );
}
