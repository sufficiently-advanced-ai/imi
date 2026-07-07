/**
 * Unit tests for nodePassesLayer — the pure layer predicate applied per node
 * in the graph page's filteredNodes memo. Covers the 3 layers across an entity
 * node, a decision signal, and an action_item signal.
 */

import { nodePassesLayer, type GraphLayers } from '../layers';
import type { DomainGraphNode } from '@/lib/api/domain';

function node(signalType?: string): DomainGraphNode {
  return {
    id: 'n1',
    entityType: signalType ? 'signal' : 'person',
    attributes: signalType ? { signal_type: signalType } : { name: 'Acme' },
  };
}

const entity = node();
const decision = node('decision');
const actionItem = node('action_item');

describe('nodePassesLayer', () => {
  it("entities layer: keeps entity nodes, drops all signals", () => {
    const layer: GraphLayers = 'entities';
    expect(nodePassesLayer(entity, layer)).toBe(true);
    expect(nodePassesLayer(decision, layer)).toBe(false);
    expect(nodePassesLayer(actionItem, layer)).toBe(false);
  });

  it("decisions layer: keeps entity nodes + decisions, drops other signals", () => {
    const layer: GraphLayers = 'decisions';
    expect(nodePassesLayer(entity, layer)).toBe(true);
    expect(nodePassesLayer(decision, layer)).toBe(true);
    expect(nodePassesLayer(actionItem, layer)).toBe(false);
  });

  it("signals layer: keeps everything", () => {
    const layer: GraphLayers = 'signals';
    expect(nodePassesLayer(entity, layer)).toBe(true);
    expect(nodePassesLayer(decision, layer)).toBe(true);
    expect(nodePassesLayer(actionItem, layer)).toBe(true);
  });

  it('treats a node with no attributes as an entity (always passes)', () => {
    const bare = { id: 'x', entityType: 'person', attributes: {} } as DomainGraphNode;
    expect(nodePassesLayer(bare, 'entities')).toBe(true);
    expect(nodePassesLayer(bare, 'decisions')).toBe(true);
    expect(nodePassesLayer(bare, 'signals')).toBe(true);
  });
});
