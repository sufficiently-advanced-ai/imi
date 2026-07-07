import { cn } from "@/lib/utils";

export function PageContainer({
  width = "default",
  className,
  children,
}: {
  width?: "default" | "narrow" | "wide";
  className?: string;
  children: React.ReactNode;
}) {
  // "default" and "wide" both impose no max-width today; "wide" is reserved so
  // callers can declare intent before a wide breakpoint is defined.
  const widths = { narrow: "max-w-3xl mx-auto", default: "", wide: "" };
  return <div className={cn("p-6", widths[width], className)}>{children}</div>;
}
