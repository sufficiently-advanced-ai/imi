'use client';

import { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Download } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { exportSearchResults, type SearchResult } from "@/lib/api/knowledge-explorer";

interface ExportButtonProps {
  results: SearchResult[];
  disabled?: boolean;
}

export function ExportButton({ results, disabled }: ExportButtonProps) {
  const [format, setFormat] = useState<'json' | 'csv'>('csv');

  const handleExport = () => {
    if (results.length === 0) return;
    exportSearchResults(results, format);
  };

  return (
    <div className="flex items-center gap-1">
      <Select value={format} onValueChange={(v) => setFormat(v as 'json' | 'csv')}>
        <SelectTrigger className="w-[80px] h-9">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="csv">CSV</SelectItem>
          <SelectItem value="json">JSON</SelectItem>
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        size="sm"
        onClick={handleExport}
        disabled={disabled || results.length === 0}
      >
        <Download className="h-4 w-4 mr-1" />
        Export
      </Button>
    </div>
  );
}
