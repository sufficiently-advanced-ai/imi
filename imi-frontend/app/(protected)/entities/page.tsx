'use client';

import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { useNavigation } from '@/lib/hooks/useNavigation';
import { EntityList } from '@/components/entities/EntityList';
import { useDomain } from '@/contexts/DomainContext';
import { useDomainSchema } from '@/lib/hooks/useDomainSchema';
import { deleteEntity } from '@/lib/api/entities';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertCircle, Database, RefreshCw } from 'lucide-react';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { PageHeader } from '@/components/ui/page-header';
import { PageContainer } from '@/components/ui/page-container';
import { useToast } from '@/components/ui/use-toast';

/**
 * Loading skeleton for the entities page
 */
function EntitiesPageSkeleton() {
  return (
    <div className="space-y-6">
      {/* Tabs skeleton */}
      <div className="flex gap-2">
        {[1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-9 w-24" />
        ))}
      </div>

      {/* Controls skeleton */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-col sm:flex-row gap-4">
            <Skeleton className="h-10 flex-1" />
            <Skeleton className="h-10 w-[200px]" />
            <Skeleton className="h-10 w-[150px]" />
            <Skeleton className="h-10 w-32" />
          </div>
        </CardContent>
      </Card>

      {/* Table skeleton */}
      <Card>
        <CardContent className="p-0">
          <div className="border-b border-border p-4">
            <div className="flex gap-4">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-4 w-24" />
            </div>
          </div>
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="border-b border-border/50 p-4 last:border-b-0">
              <div className="flex gap-4">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-5 w-48" />
                <Skeleton className="h-5 w-24" />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

export default function EntitiesPage() {
  const { navigate, replace } = useNavigation();
  const searchParams = useSearchParams();
  const { toast } = useToast();
  const { isLoading: domainLoading, error: domainError, uiLabels } = useDomain();
  const { schema, loading: schemaLoading, error: schemaError, refreshSchema } = useDomainSchema();

  // Get initial entity type from URL params
  const availableEntityTypes = schema ? Object.keys(schema.entities || {}) : [];
  const initialEntityType = searchParams.get('type') || availableEntityTypes[0] || '';

  const [currentEntityType, setCurrentEntityType] = useState(initialEntityType);

  // Delete confirmation dialog state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [entityToDelete, setEntityToDelete] = useState<string | null>(null);

  // Update URL when entity type changes
  const handleEntityTypeChange = (entityType: string) => {
    setCurrentEntityType(entityType);
    const params = new URLSearchParams(searchParams.toString());
    params.set('type', entityType);
    replace(`/entities?${params.toString()}`);
  };

  // Navigate to create entity page
  const handleEntityCreate = (entityType: string) => {
    navigate(`/entities/${entityType}/new`);
  };

  // Navigate to edit entity page
  const handleEntitySelect = (entityId: string) => {
    const currentParams = searchParams.toString();
    const backUrl = currentParams ? `/entities?${currentParams}` : '/entities';
    navigate(`/entities/${currentEntityType}/${entityId}?back=${encodeURIComponent(backUrl)}`);
  };

  // Handle entity update navigation
  const handleEntityUpdate = (entityId: string) => {
    handleEntitySelect(entityId);
  };

  // Handle entity deletion with confirmation
  const handleEntityDelete = (entityId: string) => {
    setEntityToDelete(entityId);
    setDeleteDialogOpen(true);
  };

  // Perform the actual deletion after confirmation
  const confirmDelete = async () => {
    if (!entityToDelete) return;

    try {
      await deleteEntity(entityToDelete);
      toast({
        title: "Entity deleted",
        description: "The entity has been successfully deleted.",
      });
      // Refresh the page to update the list
      window.location.reload();
    } catch (error) {
      console.error('Failed to delete entity:', error);
      toast({
        title: "Failed to delete",
        description: "Could not delete the entity. Please try again.",
        variant: "destructive",
      });
    } finally {
      setEntityToDelete(null);
      setDeleteDialogOpen(false);
    }
  };

  // Update current entity type when schema changes
  useEffect(() => {
    if (schema && schema.entities && !schema.entities[currentEntityType]) {
      const availableTypes = Object.keys(schema.entities);
      if (availableTypes.length > 0) {
        handleEntityTypeChange(availableTypes[0]);
      }
    }
  }, [schema, currentEntityType]);

  const isLoading = domainLoading || schemaLoading;
  const error = domainError || schemaError;

  // Render content based on state
  const renderContent = () => {
    // Loading state
    if (isLoading && !schema) {
      return <EntitiesPageSkeleton />;
    }

    // Error state
    if (error && !schema) {
      return (
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Unable to Load Entities
            </div>
            <div className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
              {error}
            </div>
            <Button onClick={refreshSchema} variant="default" disabled={isLoading}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Try Again
            </Button>
          </CardContent>
        </Card>
      );
    }

    // No schema available
    if (!schema || !schema.entities || Object.keys(schema.entities).length === 0) {
      return (
        <Card>
          <CardContent className="py-12 text-center">
            <Database className="h-12 w-12 mx-auto text-muted-foreground/50 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              No Entity Types Available
            </div>
            <div className="text-sm text-muted-foreground mb-6">
              No entity types are defined in the current domain schema.
            </div>
            <Button onClick={refreshSchema} disabled={isLoading}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh Schema
            </Button>
          </CardContent>
        </Card>
      );
    }

    // Normal content
    return (
      <EntityList
        domainConfig={schema}
        entityType={currentEntityType}
        onEntitySelect={handleEntitySelect}
        onEntityCreate={handleEntityCreate}
        onEntityUpdate={handleEntityUpdate}
        onEntityDelete={handleEntityDelete}
        onEntityTypeFilter={handleEntityTypeChange}
        loading={isLoading}
        error={error}
      />
    );
  };

  return (
    <PageContainer className="space-y-6">
      {/* Page header */}
      <PageHeader
        title={uiLabels?.entity_label ?? "Entities"}
        actions={
          <Button
            onClick={refreshSchema}
            variant="outline"
            size="icon"
            disabled={isLoading}
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          </Button>
        }
      />

      {/* Content */}
      {renderContent()}

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        title="Delete Entity"
        description="Are you sure you want to delete this entity? This action cannot be undone."
        confirmText="Delete"
        cancelText="Cancel"
        onConfirm={confirmDelete}
        variant="destructive"
      />
    </PageContainer>
  );
}
