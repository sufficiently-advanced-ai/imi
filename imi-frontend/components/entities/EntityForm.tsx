/**
 * Entity Form Component
 * 
 * Dynamic form generator for creating and editing entities based on domain schema.
 * Handles validation, relationships, and submission.
 */

'use client';

import React, { useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { AttributeInput } from './AttributeInput';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertCircle, Loader2, Save, X } from 'lucide-react';
import { Entity, DomainSchema, CreateEntityData, UpdateEntityData } from '@/lib/api/entities';
import { useEntityForm } from '@/lib/hooks/useEntityForm';

interface EntityFormProps {
  domainConfig: DomainSchema;
  entityType: string;
  entity?: Entity | null;
  onSubmit?: (data: CreateEntityData | UpdateEntityData) => Promise<void>;
  onCancel?: () => void;
  loading?: boolean;
  className?: string;
}

export const EntityForm: React.FC<EntityFormProps> = ({
  domainConfig,
  entityType,
  entity = null,
  onSubmit,
  onCancel,
  loading: externalLoading = false,
  className
}) => {
  const {
    formData,
    errors,
    warnings,
    isValid,
    loading: formLoading,
    isSubmitting,
    isDirty,
    setValue,
    handleSubmit: formHandleSubmit,
    handleCancel: formHandleCancel,
    resetForm,
    entitySchema
  } = useEntityForm({
    domainConfig,
    entityType,
    entity,
    onSubmit,
    onCancel
  });

  const isLoading = externalLoading || formLoading || isSubmitting;

  // Group attributes for better organization
  const attributeGroups = useMemo(() => {
    if (!entitySchema?.attributes) return { required: [], optional: [] };

    const attributes = Object.entries(entitySchema.attributes);
    const required = attributes.filter(([, config]) => config.required);
    const optional = attributes.filter(([, config]) => !config.required);

    return { required, optional };
  }, [entitySchema]);

  // Get field-specific errors
  const getFieldError = (fieldName: string) => {
    return errors.find(error => error.field === fieldName)?.message;
  };

  // Get field-specific warnings
  const getFieldWarning = (fieldName: string) => {
    return warnings.find(warning => warning.field === fieldName)?.message;
  };

  // General errors (not field-specific)
  const generalErrors = errors.filter(error => error.field === 'general');

  if (!entitySchema) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          Schema not found for entity type: {entityType}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <Card className={className}>
      <CardContent className="py-6 space-y-6">
        {/* Unsaved changes indicator */}
        {isDirty && (
          <div className="text-sm text-muted-foreground">
            You have unsaved changes
          </div>
        )}
        {/* General errors */}
        {generalErrors.length > 0 && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              {generalErrors.map(error => error.message).join('; ')}
            </AlertDescription>
          </Alert>
        )}

        {/* General warnings */}
        {warnings.filter(w => w.field === 'general').length > 0 && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              {warnings.filter(w => w.field === 'general').map(w => w.message).join('; ')}
            </AlertDescription>
          </Alert>
        )}

        {/* Required fields section */}
        {attributeGroups.required.length > 0 && (
          <div className="space-y-4">
            <h3 className="text-lg font-medium">Required Information</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {attributeGroups.required.map(([attributeName, attributeConfig]) => (
                <div key={attributeName}>
                  <AttributeInput
                    attribute={attributeConfig}
                    value={formData[attributeName]}
                    onChange={(value) => setValue(attributeName, value)}
                    label={attributeName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                    error={getFieldError(attributeName)}
                    disabled={isLoading}
                  />
                  {getFieldWarning(attributeName) && (
                    <div className="text-sm text-amber-600 dark:text-amber-400 mt-1">
                      {getFieldWarning(attributeName)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Optional fields section */}
        {attributeGroups.optional.length > 0 && (
          <div className="space-y-4">
            <h3 className="text-lg font-medium">Additional Information</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {attributeGroups.optional.map(([attributeName, attributeConfig]) => (
                <div key={attributeName}>
                  <AttributeInput
                    attribute={attributeConfig}
                    value={formData[attributeName]}
                    onChange={(value) => setValue(attributeName, value)}
                    label={attributeName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                    error={getFieldError(attributeName)}
                    disabled={isLoading}
                  />
                  {getFieldWarning(attributeName) && (
                    <div className="text-sm text-amber-600 dark:text-amber-400 mt-1">
                      {getFieldWarning(attributeName)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Form actions */}
        <div className="flex items-center justify-between pt-6 border-t">
          <div className="flex items-center gap-2">
            {onCancel && (
              <Button
                type="button"
                variant="outline"
                onClick={formHandleCancel}
                disabled={isLoading}
              >
                <X className="h-4 w-4 mr-2" />
                Cancel
              </Button>
            )}
            
            {isDirty && (
              <Button
                type="button"
                variant="ghost"
                onClick={resetForm}
                disabled={isLoading}
                className="text-muted-foreground"
              >
                Reset
              </Button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Button
              type="submit"
              onClick={formHandleSubmit}
              disabled={isLoading || !isValid}
              className="min-w-[100px]"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="h-4 w-4 mr-2" />
                  Save
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Form validation summary - only show count since errors are inline */}
        {errors.filter(e => e.field !== 'general').length > 1 && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              Please fix {errors.filter(e => e.field !== 'general').length} validation errors above
            </AlertDescription>
          </Alert>
        )}
        
        {/* Show general errors that aren't field-specific */}
        {errors.filter(e => e.field === 'general').length > 0 && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              {errors.find(e => e.field === 'general')?.message}
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
};