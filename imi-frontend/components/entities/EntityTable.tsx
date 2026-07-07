/**
 * Entity Table Component
 * 
 * Displays entities in a sortable table format with actions.
 * Dynamically builds columns based on entity schema.
 */

'use client';

import React, { useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { 
  ChevronUp, 
  ChevronDown, 
  Edit, 
  Trash2, 
  ExternalLink,
  Calendar,
  Check,
  X
} from 'lucide-react';
import { Entity, DomainSchema } from '@/lib/api/entities';
import { format, parseISO } from 'date-fns';
import { cn } from '@/lib/utils';

interface EntityTableProps {
  domainConfig: DomainSchema;
  entities: Entity[];
  entityType: string;
  onSort?: (field: string, direction: 'asc' | 'desc') => void;
  onRowAction?: (action: 'edit' | 'delete' | 'view', entityId: string) => void;
  sortField?: string;
  sortDirection?: 'asc' | 'desc';
  loading?: boolean;
  className?: string;
}

export const EntityTable: React.FC<EntityTableProps> = ({
  domainConfig,
  entities,
  entityType,
  onSort,
  onRowAction,
  sortField,
  sortDirection,
  loading = false,
  className
}) => {
  const entitySchema = domainConfig?.entities?.[entityType];

  // Build columns from entity schema
  const columns = useMemo(() => {
    if (!entitySchema?.attributes) return [];

    // Handle both array and object formats for attributes
    let attributesArray: Array<[string, any]> = [];
    
    if (Array.isArray(entitySchema.attributes)) {
      // If attributes is an array, convert to entries format
      attributesArray = entitySchema.attributes.map(attr => [attr.name, attr]);
    } else {
      // If attributes is an object, use Object.entries
      attributesArray = Object.entries(entitySchema.attributes);
    }
    
    // Sort columns by importance: required first, then by type
    return attributesArray
      .sort(([, a], [, b]) => {
        if (a.required !== b.required) return b.required ? 1 : -1;
        return 0;
      })
      .slice(0, 6); // Limit to 6 columns for readability
  }, [entitySchema]);

  // Format cell value based on attribute type
  const formatCellValue = (value: any, attributeType: string, _enumValues?: string[]) => {
    if (value === null || value === undefined || value === '') {
      return <span className="text-muted-foreground">—</span>;
    }

    switch (attributeType) {
      case 'boolean':
        return (
          <div className="flex items-center">
            {value ? (
              <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <X className="h-4 w-4 text-destructive" />
            )}
          </div>
        );

      case 'date':
        if (typeof value === 'string') {
          try {
            return format(parseISO(value), 'MMM d, yyyy');
          } catch {
            /* noop */
          }
        }
        return String(value);

      case 'datetime':
        if (typeof value === 'string') {
          try {
            return (
              <div className="flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                {format(parseISO(value), 'MMM d, yyyy HH:mm')}
              </div>
            );
          } catch {
            /* noop */
          }
        }
        return String(value);

      case 'number':
        return typeof value === 'number' ? value.toLocaleString() : value;

      case 'enum':
        return (
          <Badge variant="outline" className="text-xs">
            {value}
          </Badge>
        );

      case 'string':
      default: {
        // Truncate long strings
        const str = String(value);
        if (str.length > 50) {
          return (
            <span title={str}>
              {str.substring(0, 47)}...
            </span>
          );
        }
        return str;
      }
    }
  };

  const handleSort = (field: string) => {
    if (!onSort) return;
    
    const newDirection = sortField === field && sortDirection === 'asc' ? 'desc' : 'asc';
    onSort(field, newDirection);
  };

  const getSortIcon = (field: string) => {
    if (sortField !== field) return null;
    return sortDirection === 'asc' ? (
      <ChevronUp className="h-4 w-4" />
    ) : (
      <ChevronDown className="h-4 w-4" />
    );
  };

  if (!entitySchema) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        No schema available for entity type: {entityType}
      </div>
    );
  }

  if (entities.length === 0) {
    return (
      <div className="text-center py-8 border border-dashed rounded-lg">
        <div className="text-muted-foreground mb-2">
          No entities found
        </div>
        <p className="text-sm text-muted-foreground">
          {loading ? 'Loading entities...' : 'Create your first entity to get started'}
        </p>
      </div>
    );
  }

  return (
    <div className={cn('border rounded-lg overflow-hidden', className)}>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-muted/50">
            <tr>
              {columns.map(([attributeName, attributeConfig]) => (
                <th
                  key={attributeName}
                  className={cn(
                    'px-4 py-3 text-left text-sm font-medium text-muted-foreground',
                    onSort && 'cursor-pointer hover:text-foreground'
                  )}
                  onClick={() => onSort && handleSort(attributeName)}
                >
                  <div className="flex items-center gap-2">
                    <span>
                      {attributeName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                    </span>
                    {attributeConfig.required && (
                      <span className="text-destructive">*</span>
                    )}
                    {getSortIcon(attributeName)}
                  </div>
                </th>
              ))}
              {onRowAction && (
                <th className="px-4 py-3 text-right text-sm font-medium text-muted-foreground">
                  Actions
                </th>
              )}
            </tr>
          </thead>
          <tbody className="bg-background">
            {entities.map((entity, index) => (
              <tr
                key={entity.id}
                className={cn(
                  'border-t hover:bg-muted/50',
                  index === 0 && 'border-t-0'
                )}
                role="row"
                aria-label={`${entitySchema?.label || entitySchema?.name || entityType}: ${entity.attributes?.name || entity.attributes?.title || `ID ${entity.id}`}`}
              >
                {columns.map(([attributeName, attributeConfig]) => (
                  <td key={attributeName} className="px-4 py-3 text-sm">
                    {formatCellValue(
                      entity.attributes?.[attributeName],
                      attributeConfig.type,
                      attributeConfig.enum || attributeConfig.values
                    )}
                  </td>
                ))}
                {onRowAction && (
                  <td className="px-4 py-3 text-sm">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onRowAction('view', entity.id)}
                        aria-label={`View ${entity.id}`}
                      >
                        <ExternalLink className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onRowAction('edit', entity.id)}
                        aria-label={`Edit ${entity.id}`}
                      >
                        <Edit className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onRowAction('delete', entity.id)}
                        className="text-destructive hover:text-destructive"
                        aria-label={`Delete ${entity.id}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      {loading && (
        <div className="border-t bg-muted/25 px-4 py-3 text-center">
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary mx-auto"></div>
        </div>
      )}
    </div>
  );
};