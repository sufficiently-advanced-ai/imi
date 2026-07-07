/**
 * Create Entity Page
 *
 * Handles entity creation with form validation and domain schema integration.
 */

'use client';

import React, { useState } from 'react';
import { useNavigation } from '@/lib/hooks/useNavigation';
import { EntityForm } from '@/components/entities/EntityForm';
import { useDomainSchema } from '@/lib/hooks/useDomainSchema';
import { createEntity, CreateEntityData } from '@/lib/api/entities';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft, AlertCircle, RefreshCw } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { PageContainer } from '@/components/ui/page-container';

interface CreateEntityPageProps {
  params: {
    type: string;
  };
}

/**
 * Loading skeleton for the create entity page
 */
function CreateEntitySkeleton() {
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
      <div className="space-y-2">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-72" />
      </div>

      {/* Form skeleton */}
      <Card>
        <CardContent className="py-6 space-y-6">
          <div className="space-y-4">
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
          </div>
          <div className="flex justify-between pt-6 border-t">
            <Skeleton className="h-10 w-24" />
            <Skeleton className="h-10 w-24" />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function CreateEntityPage({ params }: CreateEntityPageProps) {
  const { type: entityType } = params;
  const { navigate, back } = useNavigation();
  const { schema, loading: schemaLoading, error: schemaError, refreshSchema } = useDomainSchema();
  const { toast } = useToast();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Check if entity type is valid
  const isValidEntityType = schema?.entities && entityType in schema.entities;

  const handleSubmit = async (data: CreateEntityData) => {
    try {
      setLoading(true);
      setError(null);

      const newEntity = await createEntity(data);

      toast({
        title: "Entity Created",
        description: `Successfully created ${schema?.entities[entityType]?.name || entityType}`,
      });

      // Navigate to the new entity's edit page
      navigate(`/entities/${entityType}/${newEntity.id}`);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to create entity';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    back();
  };

  // Loading state
  if (schemaLoading && !schema) {
    return (
      <PageContainer className="max-w-2xl mx-auto space-y-6">
        <CreateEntitySkeleton />
      </PageContainer>
    );
  }

  // Schema error
  if (schemaError && !schema) {
    return (
      <PageContainer className="max-w-2xl mx-auto space-y-6">
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

  // Invalid entity type
  if (!isValidEntityType) {
    return (
      <PageContainer className="max-w-2xl mx-auto space-y-6">
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Invalid Entity Type
            </div>
            <div className="text-sm text-muted-foreground mb-6">
              The entity type &quot;{entityType}&quot; is not defined in the current domain.
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

  const entitySchema = schema?.entities?.[entityType];
  const entityName = entitySchema?.name || entityType;

  return (
    <PageContainer className="max-w-2xl mx-auto space-y-6">
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
          <span className="text-foreground font-medium">Create {entityName}</span>
        </div>
      </nav>

      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          Create {entityName}
        </h1>
        <p className="text-muted-foreground">
          Fill in the information below to create a new {entityName.toLowerCase()}
        </p>
      </div>

      {/* Error display */}
      {error && (
        <Card className="border-destructive/50">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
              <div>
                <div className="font-medium text-foreground">Failed to create entity</div>
                <div className="text-sm text-muted-foreground">{error}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Entity form */}
      <EntityForm
        domainConfig={schema!}
        entityType={entityType}
        entity={null}
        onSubmit={handleSubmit}
        onCancel={handleCancel}
        loading={loading}
      />
    </PageContainer>
  );
}