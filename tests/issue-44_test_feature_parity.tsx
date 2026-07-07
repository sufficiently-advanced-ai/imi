/**
 * Issue #44 TDD Tests: Feature Parity with ConnectionGraph
 * Tests for Phase 3: Ensuring all existing features work in CytoscapeGraph
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import CytoscapeGraph from '@/components/insights/CytoscapeGraph';

// Mock cytoscape with more detailed mock functions
const mockCytoscapeInstance = {
  ready: jest.fn((callback) => callback()),
  on: jest.fn(),
  off: jest.fn(),
  destroy: jest.fn(),
  nodes: jest.fn(() => ({
    length: 3,
    filter: jest.fn(() => ({ length: 2 })),
    style: jest.fn(),
    show: jest.fn(),
    hide: jest.fn()
  })),
  edges: jest.fn(() => ({
    length: 2,
    style: jest.fn(),
    show: jest.fn(),
    hide: jest.fn()
  })),
  layout: jest.fn(() => ({ run: jest.fn() })),
  fit: jest.fn(),
  zoom: jest.fn(() => 1),
  pan: jest.fn(() => ({ x: 0, y: 0 })),
  center: jest.fn(),
  resize: jest.fn(),
  style: jest.fn(),
  getElementById: jest.fn(),
};

jest.mock('cytoscape', () => {
  return jest.fn(() => mockCytoscapeInstance);
});

const mockNodes = [
  {
    id: 'person1',
    type: 'person',
    label: 'Alice Smith',
    metadata: { importance: 0.8, connectionCount: 5 }
  },
  {
    id: 'doc1', 
    type: 'document',
    label: 'Meeting Notes',
    metadata: { importance: 0.6, connectionCount: 3 }
  },
  {
    id: 'proj1',
    type: 'project',
    label: 'Q4 Initiative',
    metadata: { importance: 0.9, connectionCount: 8 }
  },
  {
    id: 'topic1',
    type: 'topic',
    label: 'Budget Planning',
    metadata: { importance: 0.7, connectionCount: 4 }
  }
];

const mockEdges = [
  {
    source: 'person1',
    target: 'doc1',
    type: 'authored',
    strength: 0.8
  },
  {
    source: 'person1',
    target: 'proj1', 
    type: 'manages',
    strength: 0.9
  }
];

describe('CytoscapeGraph Visual Features', () => {

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('applies correct node colors based on type', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const mockCytoscape = require('cytoscape');
    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    const styles = cytoscapeCall.style;

    // Should have color styles for each node type
    const nodeStyles = styles.filter((style: any) => style.selector?.includes('node'));
    expect(nodeStyles.length).toBeGreaterThan(0);

    // Check specific color mappings match ConnectionGraph
    const personStyle = styles.find((s: any) => s.selector?.includes('[type="person"]'));
    const documentStyle = styles.find((s: any) => s.selector?.includes('[type="document"]'));
    const projectStyle = styles.find((s: any) => s.selector?.includes('[type="project"]'));
    const topicStyle = styles.find((s: any) => s.selector?.includes('[type="topic"]'));

    expect(personStyle?.style?.['background-color']).toBe('#10B981'); // green
    expect(documentStyle?.style?.['background-color']).toBe('#3B82F6'); // blue  
    expect(projectStyle?.style?.['background-color']).toBe('#F59E0B'); // amber
    expect(topicStyle?.style?.['background-color']).toBe('#8B5CF6'); // purple
  });

  test('includes node filtering by type functionality', async () => {
    const user = userEvent.setup();
    
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should have filter dropdown
    const filterSelect = screen.getByTestId('graph-filter-dropdown');
    expect(filterSelect).toBeInTheDocument();

    // Should have filter options
    await user.click(filterSelect);
    
    expect(screen.getByText('All Types')).toBeInTheDocument();
    expect(screen.getByText('Documents')).toBeInTheDocument();
    expect(screen.getByTestId('filter-person-nodes')).toBeInTheDocument();
    expect(screen.getByText('Projects')).toBeInTheDocument();
    expect(screen.getByText('Topics')).toBeInTheDocument();
  });

  test('implements zoom controls functionality', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should have zoom controls
    const zoomInBtn = screen.getByTestId('zoom-in-button');
    const zoomOutBtn = screen.getByTestId('zoom-out-button');
    
    expect(zoomInBtn).toBeInTheDocument();
    expect(zoomOutBtn).toBeInTheDocument();

    // Click zoom in
    fireEvent.click(zoomInBtn);
    expect(mockCytoscapeInstance.zoom).toHaveBeenCalled();

    // Click zoom out  
    fireEvent.click(zoomOutBtn);
    expect(mockCytoscapeInstance.zoom).toHaveBeenCalled();
  });

  test('implements reset view functionality', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should have reset button (maximize icon)
    const resetBtn = screen.getByRole('button', { name: /maximize/i });
    expect(resetBtn).toBeInTheDocument();

    fireEvent.click(resetBtn);
    
    // Should call fit to reset view
    expect(mockCytoscapeInstance.fit).toHaveBeenCalled();
  });

  test('displays legend with correct colors', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should have legend items
    expect(screen.getByText('document')).toBeInTheDocument();
    expect(screen.getByText('person')).toBeInTheDocument(); 
    expect(screen.getByText('project')).toBeInTheDocument();
    expect(screen.getByText('topic')).toBeInTheDocument();

    // Legend items should have correct colors (inline styles)
    const legendItems = screen.getAllByText(/document|person|project|topic/);
    expect(legendItems.length).toBe(4);
  });

});

describe('CytoscapeGraph Interaction Features', () => {

  test('triggers onNodeClick callback when node is clicked', async () => {
    const mockOnNodeClick = jest.fn();
    
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={mockOnNodeClick}
      />
    );

    // Mock node click event
    const onCallback = mockCytoscapeInstance.on.mock.calls.find(
      call => call[0] === 'click'
    )?.[1];

    expect(onCallback).toBeDefined();

    // Simulate node click
    const mockEvent = {
      target: {
        isNode: () => true,
        data: () => mockNodes[0]
      }
    };

    onCallback(mockEvent);
    expect(mockOnNodeClick).toHaveBeenCalledWith(mockNodes[0]);
  });

  test('shows node details on hover', async () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should have hover event handler
    const hoverCallback = mockCytoscapeInstance.on.mock.calls.find(
      call => call[0] === 'mouseover'
    )?.[1];

    expect(hoverCallback).toBeDefined();

    // Simulate hover
    const mockEvent = {
      target: {
        isNode: () => true,
        data: () => mockNodes[0]
      }
    };

    hoverCallback(mockEvent);

    // Should show tooltip/details popup
    await waitFor(() => {
      expect(screen.getByTestId('node-details-popup')).toBeInTheDocument();
    });
  });

  test('applies edge styling based on relationship strength', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const mockCytoscape = require('cytoscape');
    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    const styles = cytoscapeCall.style;

    // Should have edge styles
    const edgeStyles = styles.filter((style: any) => style.selector?.includes('edge'));
    expect(edgeStyles.length).toBeGreaterThan(0);

    // Should include width based on strength
    const edgeStyle = edgeStyles.find((s: any) => s.selector === 'edge');
    expect(edgeStyle?.style?.width).toBeDefined();
  });

  test('handles pan and drag functionality', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Cytoscape should be initialized with pan enabled
    const mockCytoscape = require('cytoscape');
    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    
    expect(cytoscapeCall.panningEnabled).toBe(true);
    expect(cytoscapeCall.userPanningEnabled).toBe(true);
  });

});

describe('CytoscapeGraph Layout Features', () => {

  test('initializes with proper layout configuration', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const mockCytoscape = require('cytoscape');
    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    
    expect(cytoscapeCall.layout).toBeDefined();
    expect(cytoscapeCall.layout.name).toBeDefined();
  });

  test('positions nodes better than simple circular layout', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const mockCytoscape = require('cytoscape');
    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    
    // Should use a more sophisticated layout than 'circle'
    expect(cytoscapeCall.layout.name).not.toBe('circle');
    expect(['cose', 'cola', 'dagre', 'grid'].includes(cytoscapeCall.layout.name)).toBe(true);
  });

  test('is responsive to container size changes', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    // Should call resize when container changes
    expect(mockCytoscapeInstance.resize).toHaveBeenCalled();
  });

});

describe('CytoscapeGraph Performance Features', () => {

  test('handles large datasets efficiently', () => {
    // Create larger dataset
    const largeNodes = Array.from({ length: 100 }, (_, i) => ({
      id: `node${i}`,
      type: i % 4 === 0 ? 'person' : i % 4 === 1 ? 'document' : i % 4 === 2 ? 'project' : 'topic',
      label: `Node ${i}`,
      metadata: { importance: Math.random(), connectionCount: Math.floor(Math.random() * 10) }
    }));

    const largeEdges = Array.from({ length: 150 }, (_, i) => ({
      source: `node${i % 100}`,
      target: `node${(i + 1) % 100}`,
      type: 'connected',
      strength: Math.random()
    }));

    expect(() => {
      render(
        <CytoscapeGraph 
          nodes={largeNodes}
          edges={largeEdges}
          onNodeClick={jest.fn()}
        />
      );
    }).not.toThrow();
  });

  test('optimizes rendering for performance', () => {
    render(
      <CytoscapeGraph 
        nodes={mockNodes}
        edges={mockEdges}
        onNodeClick={jest.fn()}
      />
    );

    const mockCytoscape = require('cytoscape');
    const cytoscapeCall = mockCytoscape.mock.calls[0][0];
    
    // Should have performance optimizations
    expect(cytoscapeCall.minZoom).toBeDefined();
    expect(cytoscapeCall.maxZoom).toBeDefined();
    expect(cytoscapeCall.wheelSensitivity).toBeDefined();
  });

});