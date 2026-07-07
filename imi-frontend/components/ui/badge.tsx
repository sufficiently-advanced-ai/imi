/**
 * @fileoverview Badge component for displaying labels, tags, and status indicators.
 * Supports multiple visual variants including default, secondary, destructive, outline,
 * success, warning, blue, and gray styles.
 */

import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * Badge variant styles using class-variance-authority.
 * Defines styling for different badge types with consistent Tailwind classes.
 */
const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground hover:bg-primary/80",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80",
        outline: "text-foreground",
        success:
          "border-transparent bg-green-100 text-green-800 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-400 dark:hover:bg-green-900/50",
        warning:
          "border-transparent bg-yellow-100 text-yellow-800 hover:bg-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-400 dark:hover:bg-yellow-900/50",
        blue:
          "border-transparent bg-blue-100 text-blue-700 hover:bg-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:hover:bg-blue-900/50",
        gray:
          "border-transparent bg-muted text-muted-foreground hover:bg-muted/80",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

/**
 * Props for the Badge component.
 * Extends standard HTML div attributes and includes variant prop for styling.
 */
export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

/**
 * Badge component for displaying labels, tags, and status indicators.
 *
 * Supports multiple visual variants to convey different meanings:
 * - default: Primary badge style
 * - secondary: Muted secondary style
 * - destructive: For errors or warnings
 * - outline: Outlined style with transparent background
 * - success: Green badge for positive states
 * - warning: Yellow badge for caution states
 * - blue: Blue badge (used for EXTERNAL meeting type)
 * - gray: Gray badge (used for INTERNAL meeting type)
 *
 * Includes data attributes for testing: data-testid and data-variant.
 *
 * @param props - Component props
 * @param props.className - Additional CSS classes to apply
 * @param props.variant - Visual variant of the badge
 * @param props.children - Content to display inside the badge
 *
 * @example
 * <Badge variant="success">Active</Badge>
 * <Badge variant="blue">EXTERNAL</Badge>
 * <Badge variant="gray">INTERNAL</Badge>
 */
function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div
      className={cn(badgeVariants({ variant }), className)}
      data-testid="badge"
      data-variant={variant}
      {...props}
    />
  )
}

export { Badge, badgeVariants }