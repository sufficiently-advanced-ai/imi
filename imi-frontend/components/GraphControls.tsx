'use client';

import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  ZoomIn,
  ZoomOut,
  Maximize,
  Download,
  Layout,
  Image as ImageIcon,
  Eye,
  EyeOff,
} from 'lucide-react';

interface GraphControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitToScreen: () => void;
  onLayoutChange: (layout: string) => void;
  onExport: (format: 'png' | 'svg') => void;
  currentLayout?: string;
  /** Whether the floating overlays (toolbar/inspector/stats/etc.) are hidden. */
  overlaysHidden?: boolean;
  /** Toggle the floating overlays. Omit to hide the toggle button. */
  onToggleOverlays?: () => void;
}

export const GraphControls: React.FC<GraphControlsProps> = ({
  onZoomIn,
  onZoomOut,
  onFitToScreen,
  onLayoutChange,
  onExport,
  currentLayout = 'force-directed',
  overlaysHidden = false,
  onToggleOverlays,
}) => {
  const [isExporting, setIsExporting] = useState(false);

  const handleExport = async (format: 'png' | 'svg') => {
    setIsExporting(true);
    try {
      await onExport(format);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <Card className="absolute top-16 right-3 z-10 border bg-background/95 p-2 shadow-lg backdrop-blur-sm">
      <TooltipProvider>
        <div className="flex flex-col gap-2">
          {/* Zoom Controls */}
          <div className="flex gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onZoomIn}
                  className="h-8 w-8 p-0"
                >
                  <ZoomIn className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Zoom In</p>
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onZoomOut}
                  className="h-8 w-8 p-0"
                >
                  <ZoomOut className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Zoom Out</p>
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onFitToScreen}
                  className="h-8 w-8 p-0"
                >
                  <Maximize className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Fit to Screen</p>
              </TooltipContent>
            </Tooltip>

            {/* Overlays Toggle */}
            {onToggleOverlays && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant={overlaysHidden ? 'default' : 'outline'}
                    onClick={onToggleOverlays}
                    className="h-8 w-8 p-0"
                  >
                    {overlaysHidden ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{overlaysHidden ? 'Show overlays (F)' : 'Hide overlays (F)'}</p>
                </TooltipContent>
              </Tooltip>
            )}
          </div>

          {/* Layout Selector */}
          <div className="w-full">
              <Select value={currentLayout} onValueChange={onLayoutChange}>
                <SelectTrigger className="h-8 text-xs">
                  <Layout className="h-4 w-4 mr-1" />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="force-directed">Organic</SelectItem>
                  <SelectItem value="hierarchical">Hierarchical</SelectItem>
                  <SelectItem value="circular">Circular</SelectItem>
                  <SelectItem value="grid">Grid</SelectItem>
                </SelectContent>
              </Select>
          </div>

          {/* Export Controls */}
          <div className="flex gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleExport('png')}
                    disabled={isExporting}
                    className="flex-1 h-8 text-xs"
                  >
                    <ImageIcon className="h-4 w-4 mr-1" />
                    PNG
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Export as PNG</p>
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleExport('svg')}
                    disabled={isExporting}
                    className="flex-1 h-8 text-xs"
                  >
                    <Download className="h-4 w-4 mr-1" />
                    SVG
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Export as SVG</p>
                </TooltipContent>
              </Tooltip>
            </div>
        </div>
      </TooltipProvider>
    </Card>
  );
};
