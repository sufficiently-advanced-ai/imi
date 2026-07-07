'use client';

import { ChatMessage as MessageType } from '@/lib/context/ChatContext';
import ReactMarkdown from 'react-markdown';
import { Sparkles, User, Search, Brain, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';

interface ChatMessageProps {
  message: MessageType;
}

// Check if content is just an interim status message (not real content)
function isInterimContent(content: string): boolean {
  if (!content) return true;
  const interimPatterns = [
    /^🚀\s*Starting/i,
    /^🤔\s*Analyzing/i,
    /^🔧\s*Using/i,
    /^✅\s*Completed/i,
  ];
  return interimPatterns.some(pattern => pattern.test(content.trim()));
}

// Tool status indicator component
function ToolStatus({ event }: { event: { type: string; tool_name?: string; result_summary?: string } }) {
  const getToolDisplay = (toolName: string) => {
    const tools: Record<string, { label: string; icon: typeof Search }> = {
      'extract_entities': { label: 'Extracting entities', icon: Search },
      'extract_patterns': { label: 'Finding patterns', icon: Brain },
      'search_knowledge': { label: 'Searching knowledge base', icon: Search },
      'generate_insights': { label: 'Generating insights', icon: Brain },
      'build_timeline': { label: 'Building timeline', icon: Search },
      'map_relationships': { label: 'Mapping relationships', icon: Brain },
      'detect_weak_signals': { label: 'Detecting signals', icon: Brain },
      'extract_risks': { label: 'Identifying risks', icon: AlertCircle },
      'extract_decisions': { label: 'Finding decisions', icon: CheckCircle2 },
    };
    return tools[toolName] || { label: toolName, icon: Search };
  };

  if (event.type === 'tool_start' && event.tool_name) {
    const tool = getToolDisplay(event.tool_name);
    const Icon = tool.icon;
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
        <Icon className="h-3 w-3 animate-pulse" />
        <span>{tool.label}...</span>
      </div>
    );
  }

  if (event.type === 'tool_complete' && event.result_summary) {
    if (event.result_summary !== 'Tool execution successful' && event.result_summary.length > 10) {
      return (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
          <CheckCircle2 className="h-3 w-3 text-green-500" />
          <span className="truncate max-w-[300px]">{event.result_summary}</span>
        </div>
      );
    }
  }

  return null;
}

// Thinking indicator component
function ThinkingIndicator({ status }: { status?: string }) {
  const statusText = {
    'starting': 'Starting...',
    'thinking': 'Thinking...',
    'using_tools': 'Analyzing...',
    'completing': 'Finishing up...',
  }[status || ''] || 'Thinking...';

  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground py-1">
      <Loader2 className="h-4 w-4 animate-spin text-primary" />
      <span>{statusText}</span>
    </div>
  );
}

export default function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const hasRealContent = message.content && !isInterimContent(message.content);
  const isProcessing = message.isPending || message.streamingStatus === 'starting' ||
                       message.streamingStatus === 'thinking' || message.streamingStatus === 'using_tools';

  // Format timestamp
  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className={`flex gap-3 py-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      {/* Avatar */}
      <div className={`flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center ${
        isUser
          ? 'bg-primary text-primary-foreground'
          : 'bg-primary/10 border border-primary/20 text-primary'
      }`}>
        {isUser ? (
          <User className="h-4 w-4" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
      </div>

      {/* Message content */}
      <div className={`flex-1 min-w-0 space-y-1 ${isUser ? 'flex flex-col items-end' : ''}`}>
        {/* Header with role and time - OUTSIDE bubble */}
        <div className={`flex items-center gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
          <span className="text-sm font-medium text-foreground">
            {isUser ? 'You' : 'imi'}
          </span>
          <span className="text-xs text-muted-foreground">
            {formatTime(message.timestamp)}
          </span>
        </div>

        {/* Message bubble */}
        <div className={`rounded-2xl px-4 py-3 inline-block ${
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted border border-border'
        } ${isUser ? 'max-w-[85%]' : 'max-w-[85%]'}`}>

          {/* Show thinking indicator when processing and no real content yet */}
          {!isUser && isProcessing && !hasRealContent && (
            <ThinkingIndicator status={message.streamingStatus} />
          )}

          {/* Show tool activity if streaming */}
          {!isUser && message.streamingEvents && message.streamingEvents.length > 0 && !hasRealContent && (
            <div className="space-y-1">
              {message.streamingEvents
                .filter(e => e.type === 'tool_start' || (e.type === 'tool_complete' && e.result_summary))
                .slice(-3) // Only show last 3 tool activities
                .map((event, index) => (
                  <ToolStatus key={index} event={event} />
                ))}
            </div>
          )}

          {/* Main content - only show if it's real content */}
          {hasRealContent && (
            <div className={`prose prose-sm max-w-none ${
              isUser
                ? 'text-white prose-invert [&_a]:text-blue-200 [&_a:hover]:text-blue-100'
                : 'prose-neutral dark:prose-invert'
            }`}>
              <ReactMarkdown
                components={{
                  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
                  ul: ({ children }) => <ul className="mb-2 last:mb-0 list-disc pl-4 space-y-1">{children}</ul>,
                  ol: ({ children }) => <ol className="mb-2 last:mb-0 list-decimal pl-4 space-y-1">{children}</ol>,
                  li: ({ children }) => <li>{children}</li>,
                  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                  code: ({ children, className }) => {
                    const isInline = !className;
                    return isInline ? (
                      <code className="px-1.5 py-0.5 rounded bg-black/10 dark:bg-white/10 text-sm font-mono">
                        {children}
                      </code>
                    ) : (
                      <code className={className}>{children}</code>
                    );
                  },
                  pre: ({ children }) => (
                    <pre className="p-3 rounded-lg bg-black/10 dark:bg-white/10 overflow-x-auto text-sm my-2">
                      {children}
                    </pre>
                  ),
                  a: ({ href, children }) => (
                    <a href={href} className="text-primary hover:underline" target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Metadata section (sources, confidence, context) - only for assistant messages with content */}
        {!isUser && hasRealContent && (message.sources?.length || message.confidence || message.contextFiles?.length) && (
          <div className="flex flex-wrap gap-1.5 mt-1">
            {/* Confidence badge */}
            {message.confidence && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-primary/10 text-primary">
                <CheckCircle2 className="h-3 w-3" />
                {message.confidence}
              </span>
            )}

            {/* Sources */}
            {message.sources && message.sources.length > 0 && message.sources.map((source, index) => (
              <span
                key={index}
                className="px-2 py-0.5 rounded-full text-xs bg-green-500/10 text-green-600 dark:text-green-400"
              >
                {source}
              </span>
            ))}

            {/* Context files */}
            {message.contextFiles && message.contextFiles.length > 0 && message.contextFiles.map(path => (
              <span
                key={path}
                className="px-2 py-0.5 rounded-full text-xs bg-muted text-muted-foreground"
                title={path}
              >
                {path.split('/').pop()}
              </span>
            ))}
          </div>
        )}

        {/* Error display */}
        {message.error && (
          <div className="flex items-center gap-2 text-sm text-destructive mt-1">
            <AlertCircle className="h-4 w-4" />
            <span>{message.error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
