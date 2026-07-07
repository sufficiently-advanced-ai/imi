"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  // Avoid hydration mismatch
  React.useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="w-full justify-start gap-3 px-3 py-2 text-sm text-muted-foreground"
      >
        <Sun className="h-4 w-4" />
        <span>Theme</span>
      </Button>
    );
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      className="w-full justify-start gap-3 px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-sidebar-hover"
    >
      {theme === "dark" ? (
        <>
          <Sun className="h-4 w-4" />
          <span>Light mode</span>
        </>
      ) : (
        <>
          <Moon className="h-4 w-4" />
          <span>Dark mode</span>
        </>
      )}
    </Button>
  );
}
