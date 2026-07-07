'use client';

import React, { useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';

interface GraphMinimapProps {
  nodes: any[];
  edges: any[];
  viewport: {
    x: number;
    y: number;
    zoom: number;
  };
  onViewportChange: (viewport: { x: number; y: number; zoom: number }) => void;
}

export const GraphMinimap: React.FC<GraphMinimapProps> = ({
  nodes,
  edges,
  viewport,
  onViewportChange,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const isDragging = useRef(false);

  useEffect(() => {
    if (!canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Read theme tokens at draw time so the minimap follows light/dark mode.
    // The CSS vars store HSL triplets like "240 5% 64%" — wrap with hsl() when
    // assigning to canvas styles. Fallbacks keep us safe before CSS loads.
    const root = getComputedStyle(document.documentElement);
    const tokenColor = (name: string, fallback: string) => {
      const raw = root.getPropertyValue(name).trim();
      return raw ? `hsl(${raw})` : fallback;
    };
    const tokenColorAlpha = (name: string, alpha: number, fallback: string) => {
      const raw = root.getPropertyValue(name).trim();
      return raw ? `hsl(${raw} / ${alpha})` : fallback;
    };
    const edgeColor = tokenColor('--border', '#d4d4d8');
    const nodeColor = tokenColor('--muted-foreground', '#71717a');
    const viewportStroke = tokenColor('--primary', '#7c3aed');
    const viewportFill = tokenColorAlpha('--primary', 0.12, 'rgba(124, 58, 237, 0.12)');

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Calculate bounds
    const bounds = calculateBounds(nodes);
    const scale = Math.min(
      canvas.width / (bounds.maxX - bounds.minX),
      canvas.height / (bounds.maxY - bounds.minY)
    ) * 0.8;

    // Draw edges
    ctx.strokeStyle = edgeColor;
    ctx.lineWidth = 0.5;
    edges.forEach(edge => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      const targetNode = nodes.find(n => n.id === edge.target);
      if (sourceNode && targetNode) {
        ctx.beginPath();
        ctx.moveTo(
          (sourceNode.x - bounds.minX) * scale + 10,
          (sourceNode.y - bounds.minY) * scale + 10
        );
        ctx.lineTo(
          (targetNode.x - bounds.minX) * scale + 10,
          (targetNode.y - bounds.minY) * scale + 10
        );
        ctx.stroke();
      }
    });

    // Draw nodes
    ctx.fillStyle = nodeColor;
    nodes.forEach(node => {
      const x = (node.x - bounds.minX) * scale + 10;
      const y = (node.y - bounds.minY) * scale + 10;
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, 2 * Math.PI);
      ctx.fill();
    });

    // Draw viewport rectangle
    // Calculate viewport dimensions based on the actual canvas viewport
    const viewportWidth = (canvas.width / viewport.zoom) * 0.2;
    const viewportHeight = (canvas.height / viewport.zoom) * 0.2;

    // Transform viewport coordinates to minimap space
    const viewportX = (-viewport.x / viewport.zoom + bounds.minX) * scale + 10;
    const viewportY = (-viewport.y / viewport.zoom + bounds.minY) * scale + 10;

    ctx.strokeStyle = viewportStroke;
    ctx.fillStyle = viewportFill;
    ctx.lineWidth = 2;

    // Draw filled rectangle
    ctx.fillRect(viewportX, viewportY, viewportWidth, viewportHeight);
    ctx.strokeRect(viewportX, viewportY, viewportWidth, viewportHeight);
  }, [nodes, edges, viewport]);

  const calculateBounds = (nodes: any[]) => {
    if (nodes.length === 0) {
      return { minX: 0, maxX: 100, minY: 0, maxY: 100 };
    }

    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;

    nodes.forEach(node => {
      minX = Math.min(minX, node.x || 0);
      maxX = Math.max(maxX, node.x || 0);
      minY = Math.min(minY, node.y || 0);
      maxY = Math.max(maxY, node.y || 0);
    });

    return { minX, maxX, minY, maxY };
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    isDragging.current = true;
    updateViewport(e);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging.current) {
      updateViewport(e);
    }
  };

  const handleMouseUp = () => {
    isDragging.current = false;
  };

  // Add global mouseup listener to handle dragging outside the component
  useEffect(() => {
    const handleGlobalMouseUp = () => {
      isDragging.current = false;
    };

    window.addEventListener('mouseup', handleGlobalMouseUp);
    
    return () => {
      window.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, []);

  const updateViewport = (e: React.MouseEvent) => {
    if (!canvasRef.current) return;

    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const bounds = calculateBounds(nodes);
    const scale = Math.min(
      canvas.width / (bounds.maxX - bounds.minX),
      canvas.height / (bounds.maxY - bounds.minY)
    ) * 0.8;

    // Get click position in minimap coordinates
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;
    
    // Convert to graph coordinates
    const graphX = ((clickX - 10) / scale) + bounds.minX;
    const graphY = ((clickY - 10) / scale) + bounds.minY;
    
    // Calculate pan values (negative because pan moves the viewport)
    const panX = -graphX * viewport.zoom;
    const panY = -graphY * viewport.zoom;

    onViewportChange({ x: panX, y: panY, zoom: viewport.zoom });
  };

  return (
    <Card className="absolute bottom-4 left-4 z-10 border bg-background/95 p-2 shadow-lg backdrop-blur-sm">
      <div className="mb-1 text-xs font-medium text-muted-foreground">Minimap</div>
      <canvas
        ref={canvasRef}
        width={150}
        height={100}
        role="img"
        aria-label="Graph minimap — drag to pan the main view"
        className="cursor-move rounded-sm border border-border"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      />
    </Card>
  );
};