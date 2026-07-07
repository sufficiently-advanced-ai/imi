import React from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import * as Collapsible from '@radix-ui/react-collapsible';

interface MetadataDisplayProps {
  metadata: Record<string, any>;
  level?: number;
}

export function MetadataDisplay({ metadata, level = 0 }: MetadataDisplayProps) {
  const [openSections, setOpenSections] = React.useState<Set<string>>(new Set());

  const toggleSection = (key: string) => {
    const newOpen = new Set(openSections);
    if (newOpen.has(key)) {
      newOpen.delete(key);
    } else {
      newOpen.add(key);
    }
    setOpenSections(newOpen);
  };

  const renderValue = (value: any, key: string): React.ReactNode => {
    // Handle null/undefined
    if (value === null || value === undefined) {
      return <span className="text-slate-400 italic">empty</span>;
    }

    // Handle arrays
    if (Array.isArray(value)) {
      if (value.length === 0) {
        return <span className="text-slate-400 italic">[]</span>;
      }
      
      // Check if all items are simple values
      const hasComplexItems = value.some(item => 
        typeof item === 'object' && item !== null
      );

      if (!hasComplexItems) {
        // Simple array - display inline
        return (
          <div className="flex flex-wrap gap-1">
            {value.map((item, idx) => (
              <span key={idx} className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full text-xs">
                {String(item)}
              </span>
            ))}
          </div>
        );
      }

      // Complex array - display as list
      return (
        <div className="ml-2 mt-1">
          {value.map((item, idx) => (
            <div key={idx} className="border-l-2 border-slate-200 pl-3 py-1">
              <div className="text-xs text-slate-500 mb-1">Item {idx + 1}</div>
              {typeof item === 'object' && item !== null ? (
                <MetadataDisplay metadata={item} level={level + 1} />
              ) : (
                <span className="text-slate-600">{String(item)}</span>
              )}
            </div>
          ))}
        </div>
      );
    }

    // Handle objects
    if (typeof value === 'object' && value !== null) {
      const isOpen = openSections.has(key);
      return (
        <Collapsible.Root open={isOpen} onOpenChange={() => toggleSection(key)}>
          <Collapsible.Trigger className="flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900">
            {isOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            <span className="text-xs text-slate-500">{Object.keys(value).length} properties</span>
          </Collapsible.Trigger>
          <Collapsible.Content>
            <div className="ml-2 mt-1 border-l-2 border-slate-200 pl-3">
              <MetadataDisplay metadata={value} level={level + 1} />
            </div>
          </Collapsible.Content>
        </Collapsible.Root>
      );
    }

    // Handle booleans
    if (typeof value === 'boolean') {
      return (
        <span className={`px-2 py-0.5 rounded-full text-xs ${
          value ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
        }`}>
          {value ? 'true' : 'false'}
        </span>
      );
    }

    // Handle dates (check if string looks like a date)
    if (typeof value === 'string') {
      const datePattern = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?/;
      if (datePattern.test(value)) {
        const date = new Date(value);
        if (!isNaN(date.getTime())) {
          return (
            <span className="text-slate-600">
              {date.toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric',
                ...(value.includes('T') ? { hour: '2-digit', minute: '2-digit' } : {})
              })}
            </span>
          );
        }
      }

      // Handle URLs
      if (value.startsWith('http://') || value.startsWith('https://')) {
        return (
          <a href={value} target="_blank" rel="noopener noreferrer" 
             className="text-blue-600 hover:text-blue-800 underline">
            {value}
          </a>
        );
      }

      // Handle long strings
      if (value.length > 100) {
        return (
          <div className="text-slate-600 text-sm">
            <p className="whitespace-pre-wrap break-words">{value}</p>
          </div>
        );
      }
    }

    // Default rendering
    return <span className="text-slate-600">{String(value)}</span>;
  };

  return (
    <div className={`space-y-2 ${level > 0 ? 'text-sm' : ''}`}>
      {Object.entries(metadata).map(([key, value]) => (
        <div key={key} className="flex items-start gap-2">
          <div className="min-w-[120px] font-medium text-slate-700">{key}:</div>
          <div className="flex-1">{renderValue(value, key)}</div>
        </div>
      ))}
    </div>
  );
}