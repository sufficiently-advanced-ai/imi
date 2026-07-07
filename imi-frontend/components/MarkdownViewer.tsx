'use client';

import { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import * as Collapsible from '@radix-ui/react-collapsible';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { parseFrontmatter, Frontmatter } from '@/lib/utils/frontmatter';
import { MetadataDisplay } from './MetadataDisplay';

interface MarkdownViewerProps {
  content: string;
  className?: string;
}

export default function MarkdownViewer({ content, className = '' }: MarkdownViewerProps) {
  const [open, setOpen] = useState(false);
  const [parsed, setParsed] = useState<{ frontmatter: Frontmatter; content: string }>({
    frontmatter: {},
    content: ''
  });

  useEffect(() => {
    if (content) {
      setParsed(parseFrontmatter(content));
    }
  }, [content]);

  const hasFrontmatter = Object.keys(parsed.frontmatter).length > 0;

  return (
    <div className={`markdown-viewer ${className}`}>
      {hasFrontmatter && (
        <Collapsible.Root className="mb-4" open={open} onOpenChange={setOpen}>
          <Collapsible.Trigger asChild>
            <button className="flex items-center gap-1 w-full p-2 text-sm font-medium text-left bg-slate-100 hover:bg-slate-200 rounded-md transition-colors">
              {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
              <span>Metadata</span>
            </button>
          </Collapsible.Trigger>
          <Collapsible.Content className="mt-2">
            <div className="p-4 bg-slate-50 rounded-md border border-slate-200">
              <MetadataDisplay metadata={parsed.frontmatter} />
            </div>
          </Collapsible.Content>
        </Collapsible.Root>
      )}

      <div className="prose prose-slate max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHighlight]}
        >
          {parsed.content}
        </ReactMarkdown>
      </div>
    </div>
  );
}