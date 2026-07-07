/**
 * Test suite for Issue #39: Knowledge Graph Page Component
 * 
 * Tests the new /graph page that displays the knowledge graph visualization
 * using the existing ConnectionGraph component with real data.
 */

import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { jest } from '@jest/globals';

// Mock the API client
const mockGetGraphVisualization = jest.fn();
jest.mock('@/lib/api/insights', () => ({
  getGraphVisualization: mockGetGraphVisualization,
}));

// Mock the ConnectionGraph component since we're testing the page integration
jest.mock('@/components/insights/ConnectionGraph', () => {
  return function MockConnectionGraph({ nodes, edges, onNodeClick }: any) {
    return (
      <div data-testid="connection-graph-mock">
        <div data-testid="node-count">{nodes.length}</div>
        <div data-testid="edge-count">{edges.length}</div>
        {nodes.map((node: any) => (
          <div key={node.id} data-testid={`node-${node.id}`}>
            {node.label} ({node.type})
          </div>
        ))}
        {edges.map((edge: any, index: number) => (
          <div key={index} data-testid={`edge-${edge.source}-${edge.target}`}>
            {edge.source} -> {edge.target} ({edge.strength})
          </div>
        ))}
        <button onClick={() => onNodeClick?.(nodes[0])} data-testid="mock-node-click">
          Click Node
        </button>
      </div>
    );
  };
});

// This will fail until we create the actual page
const GraphPage = React.lazy(() => import('@/app/graph/page'));

describe('Knowledge Graph Page', () => {
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
      },
      {
        id: 'topic:ai',
        label: 'Artificial Intelligence',
        type: 'topic', 
        metadata: { documentCount: 1, connectionCount: 0 }
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

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('should render the graph page title', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Knowledge Graph')).toBeInTheDocument();
    });
  });

  it('should load and display graph data', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByTestId('connection-graph-mock')).toBeInTheDocument();
    });

    expect(screen.getByTestId('node-count')).toHaveTextContent('3');
    expect(screen.getByTestId('edge-count')).toHaveTextContent('1');
  });

  it('should display loading state while fetching data', async () => {
    // Mock a delayed response
    mockGetGraphVisualization.mockImplementation(() => 
      new Promise(resolve => setTimeout(() => resolve(mockGraphData), 100))
    );
    
    render(<GraphPage />);
    
    expect(screen.getByText('Loading knowledge graph...')).toBeInTheDocument();
    
    await waitFor(() => {
      expect(screen.getByTestId('connection-graph-mock')).toBeInTheDocument();
    });
  });

  it('should display error state when API fails', async () => {
    mockGetGraphVisualization.mockRejectedValue(new Error('API Error'));
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByText(/failed to load knowledge graph/i)).toBeInTheDocument();
    });
  });

  it('should display empty state when no data available', async () => {
    mockGetGraphVisualization.mockResolvedValue({ nodes: [], edges: [] });
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByText(/no graph data available/i)).toBeInTheDocument();
    });
  });

  it('should refresh data when refresh button is clicked', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByTestId('connection-graph-mock')).toBeInTheDocument();
    });

    const refreshButton = screen.getByRole('button', { name: /refresh/i });
    fireEvent.click(refreshButton);
    
    expect(mockGetGraphVisualization).toHaveBeenCalledTimes(2);
  });

  it('should handle node click interactions', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByTestId('connection-graph-mock')).toBeInTheDocument();
    });

    const mockNodeClickButton = screen.getByTestId('mock-node-click');
    fireEvent.click(mockNodeClickButton);
    
    // Should show node details or perform some action
    // This depends on implementation
  });

  it('should render all node types correctly', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByTestId('node-person:john-doe')).toHaveTextContent('John Doe (person)');
      expect(screen.getByTestId('node-project:alpha')).toHaveTextContent('Project Alpha (project)');
      expect(screen.getByTestId('node-topic:ai')).toHaveTextContent('Artificial Intelligence (topic)');
    });
  });

  it('should render edges correctly', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByTestId('edge-person:john-doe-project:alpha')).toHaveTextContent('person:john-doe -> project:alpha (0.8)');
    });
  });

  it('should have proper page layout', async () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      const container = screen.getByTestId('graph-page-container');
      expect(container).toBeInTheDocument();
      expect(container).toHaveClass('h-full'); // Full height layout
    });
  });

  it('should call API on mount', () => {
    mockGetGraphVisualization.mockResolvedValue(mockGraphData);
    
    render(<GraphPage />);
    
    expect(mockGetGraphVisualization).toHaveBeenCalledTimes(1);
  });
});

describe('Graph Page Integration', () => {
  it('should integrate with ConnectionGraph component', async () => {
    const mockData = {
      nodes: [{ id: 'test', label: 'Test', type: 'person', metadata: {} }],
      edges: []
    };
    
    mockGetGraphVisualization.mockResolvedValue(mockData);
    
    render(<GraphPage />);
    
    await waitFor(() => {
      // Verify that ConnectionGraph receives the correct props
      expect(screen.getByTestId('connection-graph-mock')).toBeInTheDocument();
      expect(screen.getByTestId('node-test')).toBeInTheDocument();
    });
  });

  it('should handle large datasets efficiently', async () => {
    // Create large dataset
    const largeNodes = Array.from({ length: 100 }, (_, i) => ({
      id: `person:user-${i}`,
      label: `User ${i}`,
      type: 'person',
      metadata: { documentCount: 1, connectionCount: 1 }
    }));
    
    const largeEdges = Array.from({ length: 50 }, (_, i) => ({
      source: `person:user-${i}`,
      target: `person:user-${i + 1}`,
      type: 'collaboration',
      strength: 0.5
    }));

    mockGetGraphVisualization.mockResolvedValue({ 
      nodes: largeNodes, 
      edges: largeEdges 
    });
    
    render(<GraphPage />);
    
    await waitFor(() => {
      expect(screen.getByTestId('node-count')).toHaveTextContent('100');
      expect(screen.getByTestId('edge-count')).toHaveTextContent('50');
    });
  });
});