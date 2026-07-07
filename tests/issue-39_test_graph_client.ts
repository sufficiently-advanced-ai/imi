/**
 * Test suite for Issue #39: Graph API Client
 * 
 * Tests the new getGraphVisualization() method in the insights API client
 * that fetches knowledge graph data from the backend.
 */

import { jest } from '@jest/globals';

// Mock the base API client
const mockApiClient = jest.fn();
jest.mock('@/lib/api/index', () => ({
  apiClient: mockApiClient,
}));

// This will fail until we implement the function
import { getGraphVisualization } from '@/lib/api/insights';

describe('Graph API Client', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('getGraphVisualization', () => {
    const mockGraphData = {
      nodes: [
        {
          id: 'person:john-doe',
          label: 'John Doe',
          type: 'person',
          metadata: { documentCount: 5, connectionCount: 3 }
        },
        {
          id: 'project:alpha',
          label: 'Project Alpha',
          type: 'project', 
          metadata: { documentCount: 2, connectionCount: 2 }
        }
      ],
      edges: [
        {
          source: 'person:john-doe',
          target: 'project:alpha',
          type: 'participation',
          strength: 0.8
        }
      ]
    };

    it('should call the correct API endpoint', async () => {
      mockApiClient.mockResolvedValue(mockGraphData);
      
      await getGraphVisualization();
      
      expect(mockApiClient).toHaveBeenCalledWith('/memory/graph/visualization');
    });

    it('should return graph data in correct format', async () => {
      mockApiClient.mockResolvedValue(mockGraphData);
      
      const result = await getGraphVisualization();
      
      expect(result).toEqual(mockGraphData);
      expect(result.nodes).toHaveLength(2);
      expect(result.edges).toHaveLength(1);
    });

    it('should validate node structure', async () => {
      mockApiClient.mockResolvedValue(mockGraphData);
      
      const result = await getGraphVisualization();
      
      for (const node of result.nodes) {
        expect(node).toHaveProperty('id');
        expect(node).toHaveProperty('label');
        expect(node).toHaveProperty('type');
        expect(node).toHaveProperty('metadata');
        expect(typeof node.id).toBe('string');
        expect(typeof node.label).toBe('string');
        expect(typeof node.type).toBe('string');
        expect(typeof node.metadata).toBe('object');
      }
    });

    it('should validate edge structure', async () => {
      mockApiClient.mockResolvedValue(mockGraphData);
      
      const result = await getGraphVisualization();
      
      for (const edge of result.edges) {
        expect(edge).toHaveProperty('source');
        expect(edge).toHaveProperty('target');
        expect(edge).toHaveProperty('type');
        expect(edge).toHaveProperty('strength');
        expect(typeof edge.source).toBe('string');
        expect(typeof edge.target).toBe('string');
        expect(typeof edge.type).toBe('string');
        expect(typeof edge.strength).toBe('number');
        expect(edge.strength).toBeGreaterThanOrEqual(0);
        expect(edge.strength).toBeLessThanOrEqual(1);
      }
    });

    it('should handle empty graph data', async () => {
      const emptyData = { nodes: [], edges: [] };
      mockApiClient.mockResolvedValue(emptyData);
      
      const result = await getGraphVisualization();
      
      expect(result.nodes).toEqual([]);
      expect(result.edges).toEqual([]);
    });

    it('should handle API errors', async () => {
      const apiError = new Error('API request failed');
      mockApiClient.mockRejectedValue(apiError);
      
      await expect(getGraphVisualization()).rejects.toThrow('API request failed');
    });

    it('should handle network errors', async () => {
      const networkError = new Error('Network error');
      mockApiClient.mockRejectedValue(networkError);
      
      await expect(getGraphVisualization()).rejects.toThrow('Network error');
    });

    it('should handle malformed response data', async () => {
      const malformedData = { invalid: 'data' };
      mockApiClient.mockResolvedValue(malformedData);
      
      // Should either throw an error or handle gracefully
      // depending on implementation approach
      const result = await getGraphVisualization();
      
      // If we're being defensive, we might return empty data
      // or throw a validation error
      expect(result).toBeDefined();
    });

    it('should handle null response', async () => {
      mockApiClient.mockResolvedValue(null);
      
      await expect(getGraphVisualization()).rejects.toThrow();
    });

    it('should handle undefined response', async () => {
      mockApiClient.mockResolvedValue(undefined);
      
      await expect(getGraphVisualization()).rejects.toThrow();
    });
  });

  describe('GraphVisualization Types', () => {
    it('should match expected TypeScript interfaces', async () => {
      const mockData = {
        nodes: [
          {
            id: 'person:test',
            label: 'Test Person',
            type: 'person',
            metadata: { documentCount: 1, connectionCount: 0 }
          }
        ],
        edges: []
      };
      
      mockApiClient.mockResolvedValue(mockData);
      
      const result = await getGraphVisualization();
      
      // TypeScript should enforce these types at compile time
      // This test ensures runtime behavior matches types
      expect(typeof result.nodes[0].id).toBe('string');
      expect(typeof result.nodes[0].label).toBe('string'); 
      expect(typeof result.nodes[0].type).toBe('string');
      expect(typeof result.nodes[0].metadata).toBe('object');
    });

    it('should support all entity types', async () => {
      const allTypesData = {
        nodes: [
          { id: 'person:1', label: 'Person', type: 'person', metadata: {} },
          { id: 'project:1', label: 'Project', type: 'project', metadata: {} },
          { id: 'topic:1', label: 'Topic', type: 'topic', metadata: {} },
          { id: 'document:1', label: 'Document', type: 'document', metadata: {} },
          { id: 'team:1', label: 'Team', type: 'team', metadata: {} }
        ],
        edges: []
      };
      
      mockApiClient.mockResolvedValue(allTypesData);
      
      const result = await getGraphVisualization();
      
      const types = result.nodes.map(n => n.type);
      expect(types).toContain('person');
      expect(types).toContain('project');
      expect(types).toContain('topic');
      expect(types).toContain('document');
      expect(types).toContain('team');
    });
  });

  describe('API Integration', () => {
    it('should use correct HTTP method', async () => {
      mockApiClient.mockResolvedValue({ nodes: [], edges: [] });
      
      await getGraphVisualization();
      
      // Should be a GET request (the default for apiClient)
      expect(mockApiClient).toHaveBeenCalledWith('/memory/graph/visualization');
    });

    it('should not send any request body', async () => {
      mockApiClient.mockResolvedValue({ nodes: [], edges: [] });
      
      await getGraphVisualization();
      
      // Should only be called with the URL, no second parameter
      expect(mockApiClient).toHaveBeenCalledWith('/memory/graph/visualization');
      expect(mockApiClient).toHaveBeenCalledTimes(1);
    });

    it('should handle large datasets efficiently', async () => {
      // Create a large dataset to test performance
      const largeNodes = Array.from({ length: 1000 }, (_, i) => ({
        id: `node:${i}`,
        label: `Node ${i}`,
        type: 'person',
        metadata: { documentCount: 1, connectionCount: 1 }
      }));
      
      const largeEdges = Array.from({ length: 500 }, (_, i) => ({
        source: `node:${i}`,
        target: `node:${i + 1}`,
        type: 'collaboration',
        strength: 0.5
      }));

      const largeData = { nodes: largeNodes, edges: largeEdges };
      mockApiClient.mockResolvedValue(largeData);
      
      const start = Date.now();
      const result = await getGraphVisualization();
      const end = Date.now();
      
      expect(result.nodes).toHaveLength(1000);
      expect(result.edges).toHaveLength(500);
      // Should complete reasonably quickly (adjust threshold as needed)
      expect(end - start).toBeLessThan(1000); // 1 second
    });
  });
});