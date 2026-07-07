/**
 * Frontmatter parser utility
 * Extracts YAML frontmatter from markdown content
 */
import yaml from 'js-yaml';

export interface Frontmatter {
  [key: string]: any;
}

export interface ParsedContent {
  frontmatter: Frontmatter;
  content: string;
}

/**
 * Parse markdown content to extract frontmatter and content
 * @param rawContent The raw markdown content
 * @returns An object containing frontmatter and the content without frontmatter
 */
export function parseFrontmatter(rawContent: string): ParsedContent {
  const defaultResult = {
    frontmatter: {},
    content: rawContent
  };

  if (!rawContent) {
    return defaultResult;
  }

  // Check if the content has frontmatter (--- at the beginning)
  if (!rawContent.startsWith('---')) {
    return defaultResult;
  }

  try {
    // Find the closing frontmatter delimiter
    const endOfFrontmatter = rawContent.indexOf('---', 3);
    
    if (endOfFrontmatter === -1) {
      return defaultResult;
    }

    // Extract the frontmatter and content
    const frontmatterRaw = rawContent.substring(3, endOfFrontmatter).trim();
    const content = rawContent.substring(endOfFrontmatter + 3).trim();

    // Parse the frontmatter using js-yaml
    const frontmatter = yaml.load(frontmatterRaw) as Frontmatter || {};

    return { frontmatter, content };
  } catch (error) {
    console.error('Error parsing frontmatter:', error);
    return defaultResult;
  }
}