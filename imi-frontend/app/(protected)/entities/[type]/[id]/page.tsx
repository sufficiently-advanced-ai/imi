/**
 * Edit Entity Page
 *
 * Handles entity editing with form validation, relationship management, and deletion.
 */

'use client';

import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { useNavigation } from '@/lib/hooks/useNavigation';
import { EntityForm } from '@/components/entities/EntityForm';
import { RelationshipManager } from '@/components/entities/RelationshipManager';
import { useDomainSchema } from '@/lib/hooks/useDomainSchema';
import {
  getEntity,
  updateEntity,
  deleteEntity,
  listEntities,
  addRelationship,
  removeRelationship,
  Entity,
  UpdateEntityData
} from '@/lib/api/entities';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { ArrowLeft, AlertCircle, Trash2, RefreshCw } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { PageContainer } from '@/components/ui/page-container';

interface EditEntityPageProps {
  params: {
    type: string;
    id: string;
  };
}

/**
 * Loading skeleton for the edit entity page
 */
function EditEntitySkeleton() {
  return (
    <div className="space-y-6">
      {/* Breadcrumb skeleton */}
      <div className="flex items-center gap-2">
        <Skeleton className="h-8 w-8" />
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 w-4" />
        <Skeleton className="h-4 w-32" />
      </div>

      {/* Header skeleton */}
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-72" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-9 w-24" />
          <Skeleton className="h-9 w-24" />
        </div>
      </div>

      {/* Content skeleton */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardContent className="py-6 space-y-6">
            <Skeleton className="h-5 w-40" />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-10 w-full" />
              </div>
              <div className="space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-10 w-full" />
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-6 space-y-4">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function EditEntityPage({ params }: EditEntityPageProps) {
  const { type: entityType, id: entityId } = params;
  const { navigate } = useNavigation();
  const searchParams = useSearchParams();
  const { schema, loading: schemaLoading, error: schemaError, refreshSchema } = useDomainSchema();
  const { toast } = useToast();
  
  const [entity, setEntity] = useState<Entity | null>(null);
  const [availableEntities, setAvailableEntities] = useState<Record<string, Entity[]>>({});
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const backUrl = searchParams.get('back') || '/entities';

  // Load entity data
  useEffect(() => {
    if (entityId && schema) {
      loadEntityData();
    }
  }, [entityId, schema]);

  const loadEntityData = async () => {
    try {
      setLoading(true);
      setError(null);

      // Load the main entity
      const entityData = await getEntity(entityId);
      setEntity(entityData);

      // Load available entities for relationships
      if (schema) {
        const entityTypes = Object.keys(schema.entities);
        const entitiesPromises = entityTypes.map(async (type) => {
          try {
            const response = await listEntities({ entity_type: type, size: 100 });
            return { type, entities: response.entities };
          } catch (err) {
            console.warn(`Failed to load entities for type ${type}:`, err);
            return { type, entities: [] };
          }
        });

        const results = await Promise.all(entitiesPromises);
        const entitiesByType: Record<string, Entity[]> = {};
        results.forEach(({ type, entities }) => {
          entitiesByType[type] = entities;
        });
        setAvailableEntities(entitiesByType);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load entity';
      setError(errorMessage);
      console.error('Load entity error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdate = async (data: UpdateEntityData) => {
    try {
      setUpdating(true);
      setError(null);

      const updatedEntity = await updateEntity(entityId, data);
      setEntity(updatedEntity);
      
      toast({
        title: "Entity Updated",
        description: "Entity updated successfully",
      });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to update entity';
      setError(errorMessage);
      throw err; // Re-throw to let form handle it
    } finally {
      setUpdating(false);
    }
  };

  const handleDelete = async () => {
    try {
      setDeleting(true);
      
      await deleteEntity(entityId);
      
      toast({
        title: "Entity Deleted",
        description: "Entity deleted successfully",
      });
      
      navigate('/entities');
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete entity';
      setError(errorMessage);
      console.error('Delete entity error:', err);
    } finally {
      setDeleting(false);
      setDeleteDialogOpen(false);
    }
  };

  const handleCancel = () => {
    navigate(backUrl);
  };

  const handleAddRelationship = async (relationshipType: string, targetEntityId: string) => {
    try {
      await addRelationship(params.id, relationshipType, targetEntityId);
      // Refresh entity to get updated relationships
      const updated = await getEntity(params.id);
      setEntity(updated);
    } catch (error) {
      console.error('Failed to add relationship:', error);
      setError('Failed to add relationship. Please try again.');
    }
  };

  const handleRemoveRelationship = async (relationshipType: string, targetEntityId: string) => {
    try {
      await removeRelationship(params.id, relationshipType, targetEntityId);
      // Refresh entity to get updated relationships
      const updated = await getEntity(params.id);
      setEntity(updated);
    } catch (error) {
      console.error('Failed to remove relationship:', error);
      setError('Failed to remove relationship. Please try again.');
    }
  };

  // Loading state
  if (schemaLoading || loading) {
    return (
      <PageContainer className="max-w-4xl mx-auto space-y-6">
        <EditEntitySkeleton />
      </PageContainer>
    );
  }

  // Schema error
  if (schemaError && !schema) {
    return (
      <PageContainer className="max-w-4xl mx-auto space-y-6">
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Unable to Load Schema
            </div>
            <div className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
              {schemaError}
            </div>
            <Button onClick={refreshSchema} variant="default">
              <RefreshCw className="h-4 w-4 mr-2" />
              Try Again
            </Button>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  // Entity not found
  if (error && error.includes('not found')) {
    return (
      <PageContainer className="max-w-4xl mx-auto space-y-6">
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Entity Not Found
            </div>
            <div className="text-sm text-muted-foreground mb-6">
              The entity with ID &quot;{entityId}&quot; could not be found.
            </div>
            <Button onClick={() => navigate('/entities')} variant="default">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Entities
            </Button>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  // Other errors
  if (error && !entity) {
    return (
      <PageContainer className="max-w-4xl mx-auto space-y-6">
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Failed to Load Entity
            </div>
            <div className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
              {error}
            </div>
            <Button onClick={loadEntityData} variant="default" disabled={loading}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Try Again
            </Button>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  if (!entity || !schema) {
    return null;
  }

  const entitySchema = schema?.entities?.[entityType];
  const entityName = entitySchema?.name || entityType;
  const displayName = entity.attributes?.name || entity.id;

  return (
    <PageContainer className="max-w-4xl mx-auto space-y-6">
      {/* Breadcrumb navigation */}
      <nav role="navigation" aria-label="Breadcrumb">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Button
            variant="ghost"
            size="icon"
            onClick={handleCancel}
            className="h-8 w-8"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <button
            onClick={() => navigate('/entities')}
            className="hover:text-foreground transition-colors"
          >
            Entities
          </button>
          <span>/</span>
          <span className="text-foreground font-medium">Edit {displayName}</span>
        </div>
      </nav>

      {/* Page header with actions */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Edit {displayName}
          </h1>
          <p className="text-muted-foreground">
            {entityName} • ID: {entity.id}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={loadEntityData}
            disabled={loading}
            size="icon"
            className="h-9 w-9"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>

          <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="destructive" size="sm">
                <Trash2 className="h-4 w-4 mr-2" />
                Delete
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete Entity</DialogTitle>
                <DialogDescription>
                  Are you sure you want to delete &quot;{displayName}&quot;? This action cannot be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setDeleteDialogOpen(false)}
                  disabled={deleting}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  onClick={handleDelete}
                  disabled={deleting}
                  aria-label="Confirm delete"
                >
                  {deleting ? 'Deleting...' : 'Delete'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <Card className="border-destructive/50">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
              <div>
                <div className="font-medium text-foreground">Operation failed</div>
                <div className="text-sm text-muted-foreground">{error}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Entity form */}
        <div className="space-y-6">
          <EntityForm
            domainConfig={schema}
            entityType={entityType}
            entity={entity}
            onSubmit={handleUpdate}
            onCancel={handleCancel}
            loading={updating}
          />
        </div>

        {/* Relationship manager */}
        <div className="space-y-6">
          <RelationshipManager
            domainConfig={schema}
            entity={entity}
            availableEntities={availableEntities}
            onAddRelationship={handleAddRelationship}
            onRemoveRelationship={handleRemoveRelationship}
            loading={updating}
          />
        </div>
      </div>
    </PageContainer>
  );
}