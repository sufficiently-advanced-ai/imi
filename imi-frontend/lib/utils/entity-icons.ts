/**
 * Entity Icon Mapping Utility
 *
 * Maps YAML icon name strings (e.g., "building-2") to lucide-react components.
 * Fallback chain: explicit icon name (iconMap) → default by entity key (defaultEntityIcons) → FolderOpen.
 */

import type { LucideIcon } from "lucide-react";
import {
  Building2,
  Briefcase,
  User,
  Users,
  UserCheck,
  Target,
  ClipboardList,
  Award,
  Calendar,
  MessageSquare,
  Star,
  FileText,
  Globe,
  FolderOpen,
  Search,
  Share2,
} from "lucide-react";

/**
 * Map from kebab-case icon names (as stored in YAML) to lucide-react components.
 */
const iconMap: Record<string, LucideIcon> = {
  "building-2": Building2,
  "briefcase": Briefcase,
  "user": User,
  "users": Users,
  "user-check": UserCheck,
  "target": Target,
  "clipboard-list": ClipboardList,
  "award": Award,
  "calendar": Calendar,
  "message-square": MessageSquare,
  "star": Star,
  "file-text": FileText,
  "globe": Globe,
  "folder-open": FolderOpen,
  "search": Search,
  "share-2": Share2,
};

/**
 * Default icons for common entity key names when no explicit icon is set in YAML.
 */
const defaultEntityIcons: Record<string, LucideIcon> = {
  person: User,
  people: Users,
  contact: User,
  user: User,
  account: Building2,
  company: Building2,
  organization: Building2,
  project: Briefcase,
  team: Users,
  candidate: UserCheck,
  role: Target,
  assessment: ClipboardList,
  competency: Award,
  interview_session: Calendar,
  behavioral_example: FileText,
  competency_rating: Star,
  activity: Calendar,
  meeting: Calendar,
};

/**
 * Resolve a lucide-react icon component for an entity type.
 *
 * @param entityKey  - The entity type key (e.g., "account", "person")
 * @param iconName   - Optional explicit icon name from YAML (e.g., "building-2")
 * @returns The matching LucideIcon component
 */
export function getEntityIcon(entityKey: string, iconName?: string | null): LucideIcon {
  // 1. Explicit icon name from YAML
  if (iconName && iconMap[iconName]) {
    return iconMap[iconName];
  }

  // 2. Default by entity key
  if (defaultEntityIcons[entityKey]) {
    return defaultEntityIcons[entityKey];
  }

  // 3. Generic fallback
  return FolderOpen;
}
