'use client';

import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { useToast } from '@/components/ui/use-toast';
import { UserPreferences } from '@/lib/api/users';

interface PreferencesFormProps {
  preferences: UserPreferences;
  onSave?: (preferences: UserPreferences) => Promise<void>;
}

export default function PreferencesForm({ preferences, onSave }: PreferencesFormProps) {
  const [formData, setFormData] = useState<UserPreferences>(preferences);
  const [isSaving, setIsSaving] = useState(false);
  const [errors, setErrors] = useState<Partial<UserPreferences>>({});
  const { toast } = useToast();

  const validateForm = (): boolean => {
    const newErrors: Partial<UserPreferences> = {};
    
    if (!formData.theme) {
      newErrors.theme = 'Theme is required' as unknown as 'light' | 'dark';
    }
    
    if (!formData.displayDensity) {
      newErrors.displayDensity = 'Display density is required' as unknown as 'compact' | 'comfortable' | 'spacious';
    }
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!validateForm()) {
      return;
    }
    
    if (!onSave) {
      toast({
        title: 'Error',
        description: 'Save function not available',
        variant: 'destructive',
      });
      return;
    }
    
    setIsSaving(true);
    
    try {
      await onSave(formData);
      toast({
        title: 'Success',
        description: 'Preferences updated successfully',
      });
    } catch {
      toast({
        title: 'Error',
        description: 'Failed to update preferences',
        variant: 'destructive',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleThemeChange = (value: string) => {
    setFormData(prev => ({ ...prev, theme: value as 'light' | 'dark' }));
    if (errors.theme) {
      setErrors(prev => ({ ...prev, theme: undefined }));
    }
  };

  const handleDensityChange = (value: string) => {
    setFormData(prev => ({ 
      ...prev, 
      displayDensity: value as 'compact' | 'comfortable' | 'spacious' 
    }));
    if (errors.displayDensity) {
      setErrors(prev => ({ ...prev, displayDensity: undefined }));
    }
  };

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">Preferences</h3>
      
      <form onSubmit={handleSubmit} role="form" className="space-y-6">
        {/* Theme Selection */}
        <div className="space-y-2">
          <Label htmlFor="theme">Theme</Label>
          <Select value={formData.theme} onValueChange={handleThemeChange}>
            <SelectTrigger id="theme" aria-invalid={!!errors.theme}>
              <SelectValue placeholder="Select theme" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="light">Light</SelectItem>
              <SelectItem value="dark">Dark</SelectItem>
            </SelectContent>
          </Select>
          {errors.theme && (
            <p className="text-sm text-red-600">Theme is required</p>
          )}
        </div>

        {/* Display Density */}
        <div className="space-y-2">
          <Label htmlFor="display-density">Display Density</Label>
          <Select value={formData.displayDensity} onValueChange={handleDensityChange}>
            <SelectTrigger id="display-density" aria-invalid={!!errors.displayDensity}>
              <SelectValue placeholder="Select display density" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="compact">Compact</SelectItem>
              <SelectItem value="comfortable">Comfortable</SelectItem>
              <SelectItem value="spacious">Spacious</SelectItem>
            </SelectContent>
          </Select>
          {errors.displayDensity && (
            <p className="text-sm text-red-600">Display density is required</p>
          )}
        </div>

        {/* Notification Settings */}
        <div className="space-y-4">
          <Label>Notifications</Label>
          
          <div className="flex items-center space-x-2">
            <Checkbox
              id="email-notifications"
              checked={formData.emailNotifications}
              onCheckedChange={(checked) =>
                setFormData(prev => ({ ...prev, emailNotifications: checked === true }))
              }
            />
            <Label htmlFor="email-notifications">Email notifications</Label>
          </div>
          
          <div className="flex items-center space-x-2">
            <Checkbox
              id="push-notifications"
              checked={formData.pushNotifications}
              onCheckedChange={(checked) =>
                setFormData(prev => ({ ...prev, pushNotifications: checked === true }))
              }
            />
            <Label htmlFor="push-notifications">Push notifications</Label>
          </div>
        </div>

        {/* Save Button */}
        <Button 
          type="submit" 
          disabled={isSaving || !onSave}
          className="w-full"
        >
          {isSaving ? 'Saving...' : 'Save Preferences'}
        </Button>
      </form>
    </Card>
  );
}