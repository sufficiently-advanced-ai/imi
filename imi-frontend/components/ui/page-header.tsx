import React from 'react';

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex justify-between items-center mb-section-gap">
      <div className="page-header-left">
        <h1 className="text-page-title tracking-tight">{title}</h1>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && (
        <div className="page-header-right flex gap-2 items-center">
          {actions}
        </div>
      )}
    </header>
  );
}
