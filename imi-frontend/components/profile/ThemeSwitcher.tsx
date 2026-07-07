'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';

interface ThemeSwitcherProps {
  currentTheme?: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
}

export default function ThemeSwitcher({ currentTheme = 'light', onThemeChange }: ThemeSwitcherProps) {
  const [theme, setTheme] = useState<'light' | 'dark'>(currentTheme);

  useEffect(() => {
    try {
      const savedTheme = (typeof window !== 'undefined'
        ? localStorage.getItem('theme')
        : null) as 'light' | 'dark' | null;
      const initial = savedTheme ?? currentTheme;
      setTheme(initial);
      applyTheme(initial);
    } catch {
      // Fallback to currentTheme without touching storage
      setTheme(currentTheme);
      applyTheme(currentTheme);
    }
  }, [currentTheme]);

  const applyTheme = (newTheme: 'light' | 'dark') => {
    if (newTheme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  };

  const handleThemeToggle = () => {
    const newTheme = theme === 'light' ? 'dark' : 'light';
    setTheme(newTheme);
    applyTheme(newTheme);
    
    // Save to localStorage (best-effort)
    try {
      localStorage.setItem('theme', newTheme);
    } catch {
      // ignore
    }
    
    // Notify parent component
    if (onThemeChange) {
      onThemeChange(newTheme);
    }
  };

  return (
    <div className="flex items-center space-x-2">
      <label htmlFor="theme-switcher" className="text-sm font-medium">
        Theme
      </label>
      <Button
        id="theme-switcher"
        variant="outline"
        size="sm"
        onClick={handleThemeToggle}
        aria-pressed={theme === 'dark'}
        className="flex items-center space-x-2"
      >
        {theme === 'light' ? (
          <span>🌞 Light</span>
        ) : (
          <span>🌙 Dark</span>
        )}
      </Button>
    </div>
  );
}