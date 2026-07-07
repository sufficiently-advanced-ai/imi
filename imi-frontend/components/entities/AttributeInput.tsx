/**
 * Polymorphic Attribute Input Component
 * 
 * Renders appropriate input components based on attribute type (string, number, date, datetime, boolean, enum).
 * Supports validation, required field indicators, and proper type handling.
 */

'use client';

import React, { useCallback } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { 
  Select, 
  SelectContent, 
  SelectItem, 
  SelectTrigger, 
  SelectValue 
} from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { CalendarIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { format } from 'date-fns';
import { cn } from '@/lib/utils';

interface AttributeDefinition {
  type: 'string' | 'number' | 'date' | 'datetime' | 'boolean' | 'enum';
  required: boolean;
  values?: string[]; // For enum types
  placeholder?: string;
  min?: number;
  max?: number;
}

interface AttributeInputProps {
  attribute: AttributeDefinition;
  value: any;
  onChange: (value: any) => void;
  label: string;
  error?: string;
  disabled?: boolean;
  className?: string;
}

export const AttributeInput: React.FC<AttributeInputProps> = ({
  attribute,
  value,
  onChange,
  label,
  error,
  disabled = false,
  className
}) => {
  const { type, required, values, placeholder, min, max } = attribute;

  // Handle date formatting for display
  const formatDateValue = useCallback((val: any, includeTime: boolean = false) => {
    if (!val) return '';
    
    if (val instanceof Date) {
      return includeTime 
        ? val.toISOString().slice(0, 19) 
        : val.toISOString().slice(0, 10);
    }
    
    if (typeof val === 'string') {
      if (includeTime && val.includes('T')) {
        return val.slice(0, 19);
      } else if (!includeTime && val.includes('T')) {
        return val.slice(0, 10);
      }
      return val;
    }
    
    return '';
  }, []);

  // Handle date change
  const handleDateChange = useCallback((date: Date | undefined, includeTime: boolean = false) => {
    if (!date) {
      onChange('');
      return;
    }
    
    const isoString = includeTime 
      ? date.toISOString()
      : date.toISOString().slice(0, 10);
    onChange(isoString);
  }, [onChange]);

  const labelElement = (
    <Label htmlFor={label.toLowerCase().replace(/\s+/g, '_')} className="text-sm font-medium">
      {label}
      {required && <span className="text-destructive ml-1">*</span>}
    </Label>
  );

  const inputId = label.toLowerCase().replace(/\s+/g, '_');

  // Render different input types based on attribute type
  const renderInput = () => {
    switch (type) {
      case 'string':
        return (
          <Input
            id={inputId}
            type="text"
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder || `Enter ${label.toLowerCase()}`}
            disabled={disabled}
            className={cn(error && 'border-destructive', className)}
            aria-label={label}
          />
        );

      case 'number':
        return (
          <Input
            id={inputId}
            type="number"
            value={value ?? ''}
            onChange={(e) => {
              const val = e.target.value;
              onChange(val === '' ? null : Number(val));
            }}
            placeholder={placeholder || `Enter ${label.toLowerCase()}`}
            min={min}
            max={max}
            disabled={disabled}
            className={cn(error && 'border-destructive', className)}
            aria-label={label}
          />
        );

      case 'date':
        return (
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  'w-full justify-start text-left font-normal',
                  !value && 'text-muted-foreground',
                  error && 'border-destructive',
                  className
                )}
                disabled={disabled}
                aria-label={label}
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {value ? format(new Date(value), 'PPP') : `Select ${label.toLowerCase()}`}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0">
              <Calendar
                mode="single"
                selected={value ? new Date(value) : undefined}
                onSelect={(date) => handleDateChange(date, false)}
                initialFocus
              />
            </PopoverContent>
          </Popover>
        );

      case 'datetime':
        const datetimeValue = formatDateValue(value, true);
        return (
          <Input
            id={inputId}
            type="datetime-local"
            value={datetimeValue}
            onChange={(e) => {
              const val = e.target.value;
              if (val) {
                onChange(new Date(val).toISOString());
              } else {
                onChange('');
              }
            }}
            disabled={disabled}
            className={cn(error && 'border-destructive', className)}
            aria-label={label}
          />
        );

      case 'boolean':
        return (
          <div className="flex items-center space-x-2">
            <Checkbox
              id={inputId}
              checked={Boolean(value)}
              onCheckedChange={onChange}
              disabled={disabled}
              aria-label={label}
            />
            <Label
              htmlFor={inputId}
              className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
            >
              {label}
              {required && <span className="text-destructive ml-1">*</span>}
            </Label>
          </div>
        );

      case 'enum':
        if (!values || values.length === 0) {
          return (
            <div className="text-sm text-muted-foreground">
              No options available
            </div>
          );
        }

        return (
          <Select
            value={value || ''}
            onValueChange={onChange}
            disabled={disabled}
          >
            <SelectTrigger 
              className={cn(error && 'border-destructive', className)}
              aria-label={label}
            >
              <SelectValue placeholder={`Select ${label.toLowerCase()}`} />
            </SelectTrigger>
            <SelectContent>
              {!required && (
                <SelectItem value="">
                  <span className="text-muted-foreground">None</span>
                </SelectItem>
              )}
              {values.map((option) => (
                <SelectItem key={option} value={option}>
                  {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );

      default:
        return (
          <div className="text-sm text-muted-foreground">
            Unsupported attribute type: {type}
          </div>
        );
    }
  };

  // For boolean type, the label is handled within the input
  if (type === 'boolean') {
    return (
      <div className="space-y-2">
        {renderInput()}
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  // For all other types, render label separately
  return (
    <div className="space-y-2">
      {labelElement}
      {renderInput()}
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
};