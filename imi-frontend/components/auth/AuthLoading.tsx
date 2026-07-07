export function AuthLoading(): JSX.Element {
  return (
    <div
      className="fixed inset-0 bg-background flex items-center justify-center"
      role="status"
      aria-label="Loading"
    >
      <div className="text-center">
        <div className="inline-flex items-center justify-center h-12 w-12 rounded-xl bg-primary/10 mb-4">
          <svg
            className="h-5 w-5 text-primary animate-pulse"
            aria-hidden="true"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 3l1.912 5.813a2 2 0 0 0 1.275 1.275L21 12l-5.813 1.912a2 2 0 0 0-1.275 1.275L12 21l-1.912-5.813a2 2 0 0 0-1.275-1.275L3 12l5.813-1.912a2 2 0 0 0 1.275-1.275L12 3z" />
          </svg>
        </div>
        <p className="text-sm text-muted-foreground">Loading…</p>
        <span className="sr-only">Loading…</span>
      </div>
    </div>
  );
}