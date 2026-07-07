/**
 * Relationship Manager Component
 * 
 * Manages entity relationships with cardinality constraints and validation.
 * Allows adding/removing relationships while respecting schema rules.
 */

'use client';

import React, { useState, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { 
  Dialog, 
  DialogContent, 
  DialogHeader, 
  DialogTitle, 
  DialogTrigger 
} from '@/components/ui/dialog';
import { 
  Select, 
  SelectContent, 
  SelectItem, 
  SelectTrigger, 
  SelectValue 
} from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { 
  Plus, 
  X, 
  Users, 
  Building, 
  FolderOpen, 
  AlertCircle,
  Link
} from 'lucide-react';
import { Entity, DomainSchema } from '@/lib/api/entities';

interface RelationshipManagerProps {
  domainConfig: DomainSchema;
  entity: Entity;
  availableEntities?: Record<string, Entity[]>; // Entities by type for selection
  onAddRelationship?: (relationshipType: string, targetEntityId: string) => Promise<void>;
  onRemoveRelationship?: (relationshipType: string, targetEntityId: string) => Promise<void>;
  loading?: boolean;
  className?: string;
}

interface RelationshipSectionProps {
  relationshipType: string;
  relationshipConfig: any;
  currentValues: string | string[];
  availableTargets: Entity[];
  onAdd: (targetId: string) => void;
  onRemove: (targetId: string) => void;
  disabled: boolean;
  entitySchema: any;
}

// Helper component for individual relationship sections
const RelationshipSection: React.FC<RelationshipSectionProps> = ({
  relationshipType,
  relationshipConfig,
  currentValues,
  availableTargets,
  onAdd,
  onRemove,
  disabled,
  entitySchema: _entitySchema
}) => {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [selectedTargetId, setSelectedTargetId] = useState<string>('');

  const isMany = relationshipConfig.cardinality.includes('many');
  const currentRelationships = Array.isArray(currentValues) ? currentValues : 
                              currentValues ? [currentValues] : [];
  
  // Check if we can add more relationships based on cardinality
  const canAddMore = isMany || currentRelationships.length === 0;
  
  // Filter out already selected entities
  const availableForSelection = availableTargets.filter(
    entity => !currentRelationships.includes(entity.id)
  );

  const handleAdd = async () => {
    if (!selectedTargetId) return;
    
    await onAdd(selectedTargetId);
    setSelectedTargetId('');
    setIsDialogOpen(false);
  };

  const getRelationshipIcon = (targetType: string) => {
    switch (targetType) {
      case 'person':
        return Users;
      case 'account':
        return Building;
      case 'project':
        return FolderOpen;
      default:
        return Link;
    }
  };

  const RelationshipIcon = getRelationshipIcon(relationshipConfig.target);
  const relationshipLabel = relationshipType.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <RelationshipIcon className="h-4 w-4" />
          {relationshipLabel}
          <Badge variant="outline" className="text-xs ml-auto">
            {relationshipConfig.cardinality}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Current relationships */}
        <div className="space-y-2">
          {currentRelationships.length === 0 ? (
            <div className="text-sm text-muted-foreground py-2">
              No relationships defined
            </div>
          ) : (
            currentRelationships.map(targetId => {
              const targetEntity = availableTargets.find(e => e.id === targetId);
              return (
                <div key={targetId} className="flex items-center justify-between bg-muted/50 rounded p-2">
                  <div className="flex items-center gap-2">
                    <RelationshipIcon className="h-3 w-3" />
                    <span className="text-sm">
                      {targetEntity?.attributes?.name || targetId}
                    </span>
                    {targetEntity && (
                      <Badge variant="secondary" className="text-xs">
                        {targetEntity.entity_type}
                      </Badge>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onRemove(targetId)}
                    disabled={disabled}
                    aria-label={`Remove relationship to ${targetId}`}
                  >
                    <X className="h-3 w-3" />
                  </Button>
                </div>
              );
            })
          )}
        </div>

        {/* Add relationship button */}
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              disabled={disabled || !canAddMore || availableForSelection.length === 0}
              className="w-full"
              aria-label={`Add ${relationshipConfig.target}`}
            >
              <Plus className="h-3 w-3 mr-1" />
              Add {relationshipConfig.target}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                Add {relationshipConfig.target} to {relationshipLabel}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              {availableForSelection.length === 0 ? (
                <Alert>
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>
                    No available {relationshipConfig.target} entities to link to.
                  </AlertDescription>
                </Alert>
              ) : (
                <>
                  <Select value={selectedTargetId} onValueChange={setSelectedTargetId}>
                    <SelectTrigger>
                      <SelectValue placeholder={`Select a ${relationshipConfig.target}`} />
                    </SelectTrigger>
                    <SelectContent>
                      {availableForSelection.map(entity => (
                        <SelectItem key={entity.id} value={entity.id}>
                          <div className="flex items-center gap-2">
                            <span>{entity.attributes?.name || entity.id}</span>
                            <Badge variant="secondary" className="text-xs">
                              {entity.entity_type}
                            </Badge>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={() => setIsDialogOpen(false)}>
                      Cancel
                    </Button>
                    <Button onClick={handleAdd} disabled={!selectedTargetId}>
                      Add Relationship
                    </Button>
                  </div>
                </>
              )}
            </div>
          </DialogContent>
        </Dialog>

        {/* Cardinality constraint info */}
        {!canAddMore && (
          <div className="text-xs text-muted-foreground">
            Maximum relationships reached for this cardinality constraint
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export const RelationshipManager: React.FC<RelationshipManagerProps> = ({
  domainConfig,
  entity,
  availableEntities = {},
  onAddRelationship,
  onRemoveRelationship,
  loading = false,
  className
}) => {
  const entitySchema = domainConfig?.entities?.[entity.entity_type];
  
  // Get relationship configurations
  const relationships = useMemo(() => {
    if (!entitySchema?.relationships) return [];
    
    // Handle relationships as an array (as defined in domain config)
    if (Array.isArray(entitySchema.relationships)) {
      return entitySchema.relationships.map((rel: any) => ({
        type: rel.type,
        config: rel,
        currentValues: entity.relationships?.[rel.type] || (rel.cardinality.includes('many') ? [] : '')
      }));
    }
    
    // Fallback to object format if needed
    return Object.entries(entitySchema.relationships).map(([type, config]) => ({
      type,
      config,
      currentValues: entity.relationships?.[type] || (config.cardinality.includes('many') ? [] : '')
    }));
  }, [entitySchema, entity]);

  const handleAddRelationship = async (relationshipType: string, targetEntityId: string) => {
    if (onAddRelationship) {
      await onAddRelationship(relationshipType, targetEntityId);
    }
  };

  const handleRemoveRelationship = async (relationshipType: string, targetEntityId: string) => {
    if (onRemoveRelationship) {
      await onRemoveRelationship(relationshipType, targetEntityId);
    }
  };

  if (!entitySchema) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          No schema found for entity type: {entity.entity_type}
        </AlertDescription>
      </Alert>
    );
  }

  if (relationships.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground" data-testid="relationship-manager">
        <Link className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <div>No relationships defined for this entity type</div>
      </div>
    );
  }

  return (
    <div className={className} data-testid="relationship-manager">
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Link className="h-5 w-5" />
          <h3 className="text-lg font-medium">Relationships</h3>
        </div>
        
        <div className="grid gap-4 md:grid-cols-2">
          {relationships.map(({ type, config, currentValues }) => {
            const targetEntities = availableEntities[config.target] || [];
            
            return (
              <RelationshipSection
                key={type}
                relationshipType={type}
                relationshipConfig={config}
                currentValues={currentValues}
                availableTargets={targetEntities}
                onAdd={(targetId) => handleAddRelationship(type, targetId)}
                onRemove={(targetId) => handleRemoveRelationship(type, targetId)}
                disabled={loading}
                entitySchema={entitySchema}
              />
            );
          })}
        </div>
        
        {loading && (
          <div className="text-center py-4">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary mx-auto"></div>
            <div className="text-sm text-muted-foreground mt-2">
              Updating relationships...
            </div>
          </div>
        )}
      </div>
    </div>
  );
};