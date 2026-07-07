/**
 * Entity Form Hook
 * 
 * Manages entity form state, validation, and submission for creating and editing entities
 * based on the domain schema.
 */

import { useState, useCallback, useEffect, useMemo } from 'react';
import { Entity, CreateEntityData, UpdateEntityData, validateEntity, DomainSchema } from '@/lib/api/entities';

export interface UseEntityFormOptions {
  domainConfig: DomainSchema | null;
  entityType: string;
  entity?: Entity | null;
  onSubmit?: (data: CreateEntityData | UpdateEntityData) => Promise<void>;
  onCancel?: () => void;
}

export interface FieldError {
  field: string;
  message: string;
}

export interface UseEntityFormReturn {
  // Form data
  formData: Record<string, any>;
  relationships: Record<string, string | string[]>;
  
  // Validation
  errors: FieldError[];
  warnings: FieldError[];
  isValid: boolean;
  
  // Form state
  loading: boolean;
  isSubmitting: boolean;
  isDirty: boolean;
  
  // Actions
  setValue: (field: string, value: any) => void;
  setRelationship: (relationshipType: string, value: string | string[]) => void;
  validateForm: () => Promise<boolean>;
  handleSubmit: () => Promise<void>;
  handleCancel: () => void;
  resetForm: () => void;
  
  // Computed properties
  requiredFields: string[];
  entitySchema: any;
}

export const useEntityForm = (options: UseEntityFormOptions): UseEntityFormReturn => {
  const { domainConfig, entityType, entity, onSubmit, onCancel } = options;

  // Get entity schema from domain config
  const entitySchema = useMemo(() => {
    return domainConfig?.entities?.[entityType] || null;
  }, [domainConfig, entityType]);

  // Initialize form data from entity or schema defaults
  const initializeFormData = useCallback(() => {
    const initialData: Record<string, any> = {};
    const initialRelationships: Record<string, string | string[]> = {};

    if (entitySchema) {
      // Initialize attributes with existing values or defaults
      Object.entries(entitySchema.attributes || {}).forEach(([field, config]: [string, any]) => {
        if (entity?.attributes?.[field] !== undefined) {
          initialData[field] = entity.attributes[field];
        } else {
          // Set default values based on type
          switch (config.type) {
            case 'string':
              initialData[field] = '';
              break;
            case 'number':
              initialData[field] = config.required ? 0 : null;
              break;
            case 'boolean':
              initialData[field] = false;
              break;
            case 'date':
            case 'datetime':
              initialData[field] = '';
              break;
            case 'enum':
              initialData[field] = config.required ? (config.values?.[0] || '') : '';
              break;
            default:
              initialData[field] = '';
          }
        }
      });

      // Initialize relationships
      Object.keys(entitySchema.relationships || {}).forEach(relationshipType => {
        if (entity?.relationships?.[relationshipType] !== undefined) {
          initialRelationships[relationshipType] = entity.relationships[relationshipType];
        } else {
          const relationshipConfig = entitySchema.relationships[relationshipType];
          // Initialize based on cardinality
          if (relationshipConfig.cardinality.includes('many')) {
            initialRelationships[relationshipType] = [];
          } else {
            initialRelationships[relationshipType] = '';
          }
        }
      });
    }

    return { initialData, initialRelationships };
  }, [entitySchema, entity]);

  // State
  const [originalData, setOriginalData] = useState<Record<string, any>>({});
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [relationships, setRelationships] = useState<Record<string, string | string[]>>({});
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [warnings, setWarnings] = useState<FieldError[]>([]);
  const [loading, setLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Initialize form data when entity or schema changes
  useEffect(() => {
    const { initialData, initialRelationships } = initializeFormData();
    setOriginalData(initialData);
    setFormData(initialData);
    setRelationships(initialRelationships);
    setErrors([]);
    setWarnings([]);
  }, [initializeFormData]);

  // Computed properties
  const requiredFields = useMemo(() => {
    if (!entitySchema?.attributes) return [];
    return Object.entries(entitySchema.attributes)
      .filter(([_, config]: [string, any]) => config.required)
      .map(([field]) => field);
  }, [entitySchema]);

  const isDirty = useMemo(() => {
    return JSON.stringify(formData) !== JSON.stringify(originalData) ||
           JSON.stringify(relationships) !== JSON.stringify({});
  }, [formData, originalData, relationships]);

  const isValid = useMemo(() => {
    return errors.length === 0 && requiredFields.every(field => {
      const value = formData[field];
      return value !== undefined && value !== null && value !== '';
    });
  }, [errors, requiredFields, formData]);

  /**
   * Set form field value
   */
  const setValue = useCallback((field: string, value: any) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));

    // Clear field-specific errors
    setErrors(prev => prev.filter(error => error.field !== field));
  }, []);

  /**
   * Set relationship value
   */
  const setRelationship = useCallback((relationshipType: string, value: string | string[]) => {
    setRelationships(prev => ({
      ...prev,
      [relationshipType]: value
    }));
  }, []);

  /**
   * Validate form data
   */
  const validateForm = useCallback(async (): Promise<boolean> => {
    try {
      setLoading(true);
      setErrors([]);
      setWarnings([]);

      // Client-side validation
      const clientErrors: FieldError[] = [];

      // Check required fields
      requiredFields.forEach(field => {
        const value = formData[field];
        if (value === undefined || value === null || value === '') {
          clientErrors.push({
            field,
            message: `${field.replace(/_/g, ' ')} is required`
          });
        }
      });

      // Type-specific validation
      if (entitySchema?.attributes) {
        Object.entries(entitySchema.attributes).forEach(([field, config]: [string, any]) => {
          const value = formData[field];
          
          if (value !== undefined && value !== null && value !== '') {
            switch (config.type) {
              case 'email':
                if (typeof value === 'string' && !value.includes('@')) {
                  clientErrors.push({
                    field,
                    message: 'Invalid email format'
                  });
                }
                break;
              case 'number':
                if (isNaN(Number(value))) {
                  clientErrors.push({
                    field,
                    message: 'Must be a valid number'
                  });
                }
                break;
              case 'enum':
                if (config.values && !config.values.includes(value)) {
                  clientErrors.push({
                    field,
                    message: `Must be one of: ${config.values.join(', ')}`
                  });
                }
                break;
            }
          }
        });
      }

      setErrors(clientErrors);

      if (clientErrors.length > 0) {
        return false;
      }

      // Server-side validation
      const entityData = {
        entity_type: entityType,
        attributes: formData,
        ...(Object.keys(relationships).length > 0 && { relationships })
      };

      const validationResult = await validateEntity(entityData);
      
      if (!validationResult.valid) {
        setErrors(validationResult.errors || []);
        setWarnings(validationResult.warnings || []);
        return false;
      }

      setWarnings(validationResult.warnings || []);
      return true;
    } catch (err) {
      console.error('Validation error:', err);
      setErrors([{ field: 'general', message: 'Validation failed' }]);
      return false;
    } finally {
      setLoading(false);
    }
  }, [entityType, formData, relationships, requiredFields, entitySchema]);

  /**
   * Handle form submission
   */
  const handleSubmit = useCallback(async () => {
    if (isSubmitting) return;

    try {
      setIsSubmitting(true);

      // Validate before submitting
      const isFormValid = await validateForm();
      if (!isFormValid) {
        return;
      }

      // Prepare submission data
      const submissionData: CreateEntityData | UpdateEntityData = entity ? {
        attributes: formData,
        ...(Object.keys(relationships).length > 0 && { relationships })
      } : {
        entity_type: entityType,
        attributes: formData,
        ...(Object.keys(relationships).length > 0 && { relationships })
      };

      // Call submission handler
      if (onSubmit) {
        await onSubmit(submissionData);
      }

      // Update original data to reflect successful submission
      setOriginalData(formData);
    } catch (err) {
      console.error('Submission error:', err);
      const errorMessage = err instanceof Error ? err.message : 'Submission failed';
      setErrors([{ field: 'general', message: errorMessage }]);
    } finally {
      setIsSubmitting(false);
    }
  }, [isSubmitting, validateForm, entity, entityType, formData, relationships, onSubmit]);

  /**
   * Handle form cancellation
   */
  const handleCancel = useCallback(() => {
    if (onCancel) {
      onCancel();
    }
  }, [onCancel]);

  /**
   * Reset form to initial state
   */
  const resetForm = useCallback(() => {
    const { initialData, initialRelationships } = initializeFormData();
    setFormData(initialData);
    setRelationships(initialRelationships);
    setErrors([]);
    setWarnings([]);
  }, [initializeFormData]);

  return {
    // Form data
    formData,
    relationships,
    
    // Validation
    errors,
    warnings,
    isValid,
    
    // Form state
    loading,
    isSubmitting,
    isDirty,
    
    // Actions
    setValue,
    setRelationship,
    validateForm,
    handleSubmit,
    handleCancel,
    resetForm,
    
    // Computed properties
    requiredFields,
    entitySchema
  };
};