/**
 * Issue #44 TDD Tests: Cytoscape.js Basic Rendering
 * Tests for Phase 2: Basic rendering functionality and data structure compatibility
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import CytoscapeGraph from '@/components/insights/CytoscapeGraph';

// Mock cytoscape to avoid DOM issues in tests
jest.mock('cytoscape', () => {
  return jest.fn(() => ({
    ready: jest.fn((callback) => callback()),
    on: jest.fn(),
    off: jest.fn(),
    destroy: jest.fn(),
    nodes: jest.fn(() => ({ length: 0 })),
    edges: jest.fn(() => ({ length: 0 })),
    layout: jest.fn(() => ({ run: jest.fn() })),
    fit: jest.fn(),
    zoom: jest.fn(),
    pan: jest.fn(),
    center: jest.fn(),
  }));
});

// Test data matching existing GraphNode and GraphEdge interfaces
const mockNodes = [
  {
    id: 'node1',
    type: 'person',
    label: 'John Doe',
    metadata: { importance: 0.8, connectionCount: 5 }
  },
  {
    id: 'node2', 
    type: 'document',
    label: 'Project Plan',
    metadata: { importance: 0.6, connectionCount: 3 }
  },
  {
    id: 'node3',
    type: 'project',
    label: 'Website Redesign',
    metadata: { importance: 0.9, connectionCount: 8 }
  }
];

const mockEdges = [
  {
    source: 'node1',
    target: 'node2',
    type: 'authored',
    strength: 0.8
  },
  {
    source: 'node1',
    target: 'node3', 
    type: 'manages',
    strength: 0.9
  }
];

describe('CytoscapeGraph Basic Rendering', () => {
  
  beforeEach(() => {
    // Clear all mocks before each test
    jest.clearAllMocks();
  });

  test('renders without errors with valid data', () => {
    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={mockNodes}
          edges={mockEdges}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow();
  });

  test('creates cytoscape container element', async () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should have main container with test-id
    const container = screen.getByTestId('cytoscape-graph');
    expect(container).toBeInTheDocument();
  });

  test('handles empty node data gracefully', () => {
    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={[]}
          edges={[]}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow();

    const container = screen.getByTestId('cytoscape-graph');
    expect(container).toBeInTheDocument();
  });

  test('handles missing optional props gracefully', () => {
    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={mockNodes}
          edges={mockEdges}
        />
      );
    }).not.toThrow();
  });

  test('initializes cytoscape instance with correct configuration', () => {
    const mockCytoscape = require('cytoscape');
    
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    expect(mockCytoscape).toHaveBeenCalledWith(
      expect.objectContaining({
        container: expect.any(Object),
        elements: expect.any(Array),
        style: expect.any(Array),
        layout: expect.any(Object)
      })
    );
  });

});

describe('CytoscapeGraph Data Structure Compatibility', () => {

  test('accepts GraphNode interface correctly', () => {
    const nodeWithAllFields = {
      id: 'test-node',
      type: 'topic',
      label: 'Test Topic',
      metadata: {
        importance: 0.7,
        connectionCount: 4,
        documentCount: 2,
        customField: 'custom value'
      }
    };

    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={[nodeWithAllFields]}
          edges={[]}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow();
  });

  test('accepts GraphEdge interface correctly', () => {
    const edgeWithAllFields = {
      source: 'node1',
      target: 'node2',
      type: 'collaboration',
      strength: 0.75
    };

    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={mockNodes}
          edges={[edgeWithAllFields]}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow();
  });

  test('validates node data structure', () => {
    // Test with invalid node (missing required fields)
    const invalidNode = {
      // Missing id, type, label
      metadata: { importance: 0.5 }
    };

    // Component should handle invalid data gracefully or show error
    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={[invalidNode as any]}
          edges={[]}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow(); // Should handle gracefully, not crash
  });

  test('validates edge data structure', () => {
    // Test with invalid edge (missing required fields)
    const invalidEdge = {
      // Missing source, target, type, strength
      customField: 'value'
    };

    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={mockNodes}
          edges={[invalidEdge as any]}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow(); // Should handle gracefully
  });

  test('converts data to cytoscape format correctly', () => {
    const mockCytoscape = require('cytoscape');
    
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    const elements = cytoscapeCall.elements;

    // Should have correct number of elements
    expect(elements).toHaveLength(mockNodes.length + mockEdges.length);

    // Check node conversion
    const nodeElements = elements.filter((el: any) => el.group === 'nodes');
    expect(nodeElements).toHaveLength(mockNodes.length);
    
    // Check edge conversion  
    const edgeElements = elements.filter((el: any) => el.group === 'edges');
    expect(edgeElements).toHaveLength(mockEdges.length);
  });

});

describe('CytoscapeGraph Component Structure', () => {

  test('has proper test-id attributes', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Main container
    expect(screen.getByTestId('cytoscape-graph')).toBeInTheDocument();
    
    // Should have other testable elements
    const container = screen.getByTestId('cytoscape-graph');
    expect(container).toHaveClass('cytoscape-container'); // Custom class for styling
  });

  test('applies correct CSS classes', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const container = screen.getByTestId('cytoscape-graph');
    expect(container).toHaveClass('cytoscape-container');
  });

});

describe('CytoscapeGraph Error Handling', () => {

  test('handles cytoscape initialization failure gracefully', () => {
    // Mock cytoscape to throw error
    const mockCytoscape = require('cytoscape');
    mockCytoscape.mockImplementationOnce(() => {
      throw new Error('Cytoscape initialization failed');
    });

    // Should not crash the app
    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={mockNodes}
          edges={mockEdges}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow();
  });

  test('cleans up cytoscape instance on unmount', () => {
    const destroyMock = jest.fn();
    const mockCytoscape = require('cytoscape');
    mockCytoscape.mockReturnValue({
      ready: jest.fn((callback) => callback()),
      on: jest.fn(),
      off: jest.fn(),
      destroy: destroyMock,
      nodes: jest.fn(() => ({ length: 0 })),
      edges: jest.fn(() => ({ length: 0 })),
      layout: jest.fn(() => ({ run: jest.fn() })),
      fit: jest.fn(),
      zoom: jest.fn(),
      pan: jest.fn(),
      center: jest.fn(),
    });

    const { unmount } = render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    unmount();

    expect(destroyMock).toHaveBeenCalled();
  });

});