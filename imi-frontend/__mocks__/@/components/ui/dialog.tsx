import React from 'react';

export const Dialog = ({ open, onOpenChange, children }: any) => (
  open ? (
    <div data-testid="dialog" role="dialog">
      {children}
      <button onClick={() => onOpenChange(false)}>Close Dialog</button>
    </div>
  ) : null
);

export const DialogContent = ({ children }: any) => (
  <div data-testid="dialog-content">{children}</div>
);

export const DialogHeader = ({ children }: any) => (
  <div data-testid="dialog-header">{children}</div>
);

export const DialogTitle = ({ children }: any) => (
  <h2 data-testid="dialog-title">{children}</h2>
);
