'use client';

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { fetchMeetingContent, type MeetingContent } from '@/lib/api/meetings';
import { Eye, Search, ChevronUp, ChevronDown, X } from 'lucide-react';
import { format } from 'date-fns';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle
} from '../ui/sheet';
import MarkdownViewer from '../MarkdownViewer';

interface MeetingViewerProps {
  botId: string;
  meetingTitle?: string;
  /** Optional custom trigger element. If not provided, renders an Eye icon button. */
  trigger?: React.ReactNode;
}

export default function MeetingViewer({ botId, meetingTitle, trigger }: MeetingViewerProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [meetingContent, setMeetingContent] = useState<MeetingContent | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const hasLoadedRef = useRef<boolean>(false);

  // Transcript search state
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [currentMatchIndex, setCurrentMatchIndex] = useState<number>(0);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const matchRefs = useRef<(HTMLSpanElement | null)[]>([]);

  // Reset state when botId changes
  useEffect(() => {
    hasLoadedRef.current = false;
    if (!isOpen) {
      setMeetingContent(null);
      setError(null);
    }
  }, [botId, isOpen]);

  const loadContent = async () => {
    if (isLoading || hasLoadedRef.current) return;

    setIsLoading(true);
    setError(null);

    try {
      const content = await fetchMeetingContent(botId);
      setMeetingContent(content);
      hasLoadedRef.current = true;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load meeting content';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    if (open) {
      loadContent();
    }
  };

  const formatDateTime = (dateString: string | null): string => {
    if (!dateString) return 'N/A';
    try {
      const date = new Date(dateString);
      return format(date, 'MMM d, yyyy \'at\' h:mm a');
    } catch {
      return dateString;
    }
  };

  const formatDuration = (seconds: number | null): string => {
    if (!seconds) return 'N/A';
    const minutes = Math.floor(seconds / 60);
    return `${minutes} min`;
  };

  const getTotalEntityCount = (content: MeetingContent): number => {
    const counts = content.entity_counts;
    return counts.people + counts.projects + counts.accounts + counts.action_items + counts.decisions;
  };

  // Search functionality for transcript
  const searchMatches = useMemo(() => {
    if (!searchQuery.trim() || !meetingContent?.transcript) return [];
    const regex = new RegExp(searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    const matches: { index: number; length: number }[] = [];
    let match;
    while ((match = regex.exec(meetingContent.transcript)) !== null) {
      matches.push({ index: match.index, length: match[0].length });
    }
    return matches;
  }, [searchQuery, meetingContent?.transcript]);

  // Reset match index when search changes
  useEffect(() => {
    setCurrentMatchIndex(0);
  }, [searchQuery]);

  // Scroll to current match
  useEffect(() => {
    if (searchMatches.length > 0 && matchRefs.current[currentMatchIndex]) {
      matchRefs.current[currentMatchIndex]?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }
  }, [currentMatchIndex, searchMatches.length]);

  const navigateMatch = useCallback((direction: 'next' | 'prev') => {
    if (searchMatches.length === 0) return;
    if (direction === 'next') {
      setCurrentMatchIndex((prev) => (prev + 1) % searchMatches.length);
    } else {
      setCurrentMatchIndex((prev) => (prev - 1 + searchMatches.length) % searchMatches.length);
    }
  }, [searchMatches.length]);

  const clearSearch = useCallback(() => {
    setSearchQuery('');
    setCurrentMatchIndex(0);
  }, []);

  // Render transcript with highlighted search matches
  const highlightedTranscript = useMemo(() => {
    if (!meetingContent?.transcript) return null;
    if (!searchQuery.trim() || searchMatches.length === 0) {
      return <MarkdownViewer content={meetingContent.transcript} />;
    }

    // Split transcript and highlight matches
    const transcript = meetingContent.transcript;
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    matchRefs.current = [];

    searchMatches.forEach((match, idx) => {
      // Add text before match
      if (match.index > lastIndex) {
        parts.push(
          <span key={`text-${idx}`}>
            {transcript.slice(lastIndex, match.index)}
          </span>
        );
      }
      // Add highlighted match
      const isCurrentMatch = idx === currentMatchIndex;
      parts.push(
        <span
          key={`match-${idx}`}
          ref={(el) => { matchRefs.current[idx] = el; }}
          className={`px-0.5 rounded ${
            isCurrentMatch
              ? 'bg-yellow-400 text-black font-semibold'
              : 'bg-yellow-200 text-black'
          }`}
        >
          {transcript.slice(match.index, match.index + match.length)}
        </span>
      );
      lastIndex = match.index + match.length;
    });

    // Add remaining text
    if (lastIndex < transcript.length) {
      parts.push(<span key="text-end">{transcript.slice(lastIndex)}</span>);
    }

    return <div className="whitespace-pre-wrap font-mono text-sm">{parts}</div>;
  }, [meetingContent?.transcript, searchQuery, searchMatches, currentMatchIndex]);

  return (
    <Sheet open={isOpen} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>
        {trigger || (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            title="View meeting details"
          >
            <Eye className="h-4 w-4" />
            <span className="sr-only">View meeting details</span>
          </Button>
        )}
      </SheetTrigger>
      <SheetContent className="w-[800px] sm:max-w-[800px] p-0 overflow-y-auto">
        <SheetHeader className="p-6 pb-4 border-b sticky top-0 bg-background z-10">
          <SheetTitle className="break-words">
            {meetingContent?.title || meetingTitle || 'Meeting Details'}
          </SheetTitle>
          {meetingContent && (
            <div className="flex gap-2 mt-2">
              <Badge variant="outline">
                {meetingContent.platform || 'Unknown'}
              </Badge>
              {meetingContent.is_finalized && (
                <Badge variant="success">Processed</Badge>
              )}
            </div>
          )}
        </SheetHeader>

        <div className="p-6">
          {isLoading ? (
            <div className="animate-pulse space-y-4">
              <div className="h-4 bg-gray-200 rounded w-3/4"></div>
              <div className="h-4 bg-gray-200 rounded"></div>
              <div className="h-4 bg-gray-200 rounded w-5/6"></div>
              <div className="h-4 bg-gray-200 rounded w-1/2"></div>
              <div className="h-4 bg-gray-200 rounded w-2/3"></div>
              <div className="h-4 bg-gray-200 rounded w-4/5"></div>
            </div>
          ) : error ? (
            <div className="text-red-600 space-y-2">
              <p className="font-semibold">Error Loading Meeting</p>
              <p className="text-sm">{error}</p>
              <Button variant="outline" size="sm" onClick={loadContent}>
                Retry
              </Button>
            </div>
          ) : meetingContent ? (
            <div className="space-y-6">
              {/* Meeting Details Section */}
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-muted-foreground">📊 Meeting Details</h3>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-muted-foreground">Duration</div>
                    <div className="font-medium">{formatDuration(meetingContent.duration)}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">Participants</div>
                    <div className="font-medium">{meetingContent.participants.length} attendees</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-muted-foreground">Date</div>
                    <div className="font-medium">{formatDateTime(meetingContent.start_time)}</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-muted-foreground">Platform</div>
                    <div className="font-medium">{meetingContent.platform || 'Unknown'}</div>
                  </div>
                </div>
              </div>

              {/* Extracted Entities Section */}
              <div className="space-y-3 pt-4 border-t">
                <h3 className="text-sm font-semibold text-muted-foreground">
                  📝 Extracted Entities ({getTotalEntityCount(meetingContent)} total)
                </h3>
                <div className="space-y-3 text-sm">
                  {/* People - check both singular and plural forms, deduplicate */}
                  {((meetingContent.entities_mentioned.people?.length ?? 0) > 0 ||
                    (meetingContent.entities_mentioned.person?.length ?? 0) > 0) && (
                    <div>
                      <div className="text-muted-foreground mb-1">People</div>
                      <div className="flex flex-wrap gap-1">
                        {[...new Set([
                          ...(meetingContent.entities_mentioned.people || []),
                          ...(meetingContent.entities_mentioned.person || [])
                        ])].map((name, idx) => (
                          <Badge key={idx} variant="outline" className="text-xs">{name}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Projects - check both singular and plural forms, deduplicate */}
                  {((meetingContent.entities_mentioned.projects?.length ?? 0) > 0 ||
                    (meetingContent.entities_mentioned.project?.length ?? 0) > 0) && (
                    <div>
                      <div className="text-muted-foreground mb-1">Projects</div>
                      <div className="flex flex-wrap gap-1">
                        {[...new Set([
                          ...(meetingContent.entities_mentioned.projects || []),
                          ...(meetingContent.entities_mentioned.project || [])
                        ])].map((name, idx) => (
                          <Badge key={idx} variant="secondary" className="text-xs">{name}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Accounts - check both singular and plural forms, deduplicate */}
                  {((meetingContent.entities_mentioned.accounts?.length ?? 0) > 0 ||
                    (meetingContent.entities_mentioned.account?.length ?? 0) > 0) && (
                    <div>
                      <div className="text-muted-foreground mb-1">Accounts</div>
                      <div className="flex flex-wrap gap-1">
                        {[...new Set([
                          ...(meetingContent.entities_mentioned.accounts || []),
                          ...(meetingContent.entities_mentioned.account || [])
                        ])].map((name, idx) => (
                          <Badge key={idx} variant="blue" className="text-xs">{name}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {meetingContent.entities_mentioned.action_items?.length > 0 && (
                    <div>
                      <div className="text-muted-foreground mb-1">Action Items</div>
                      <ul className="list-disc list-inside space-y-1">
                        {meetingContent.entities_mentioned.action_items.map((item, idx) => (
                          <li key={idx} className="text-sm">{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {meetingContent.entities_mentioned.decisions?.length > 0 && (
                    <div>
                      <div className="text-muted-foreground mb-1">Decisions</div>
                      <ul className="list-disc list-inside space-y-1">
                        {meetingContent.entities_mentioned.decisions.map((item, idx) => (
                          <li key={idx} className="text-sm">{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                {getTotalEntityCount(meetingContent) === 0 && (
                  <p className="text-sm text-muted-foreground italic">No entities extracted yet</p>
                )}
              </div>

              {/* Meeting Summary Section */}
              <div className="space-y-3 pt-4 border-t">
                <h3 className="text-sm font-semibold text-muted-foreground"># Meeting Summary</h3>
                {meetingContent.body ? (
                  <div className="prose prose-sm max-w-none">
                    <MarkdownViewer content={meetingContent.body} />
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground italic">No summary available</p>
                )}
              </div>

              {/* Full Transcript Section - Window-like UI with Search */}
              {meetingContent.transcript && (
                <div className="pt-4 border-t">
                  {/* Window-like container */}
                  <div className="border rounded-lg shadow-md overflow-hidden bg-background">
                    {/* Window title bar */}
                    <div className="bg-muted/80 border-b px-3 py-2 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold">📜 Full Transcript</span>
                        <span className="text-xs text-muted-foreground">
                          ({Math.round(meetingContent.transcript.length / 1000)}k chars)
                        </span>
                      </div>
                      {/* Search input */}
                      <div className="flex items-center gap-1">
                        <div className="relative">
                          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
                          <Input
                            type="text"
                            placeholder="Search transcript..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="h-7 w-48 pl-7 pr-7 text-xs"
                          />
                          {searchQuery && (
                            <button
                              onClick={clearSearch}
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                        {searchMatches.length > 0 && (
                          <>
                            <span className="text-xs text-muted-foreground min-w-[60px] text-center">
                              {currentMatchIndex + 1} / {searchMatches.length}
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => navigateMatch('prev')}
                              title="Previous match"
                            >
                              <ChevronUp className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              onClick={() => navigateMatch('next')}
                              title="Next match"
                            >
                              <ChevronDown className="h-3 w-3" />
                            </Button>
                          </>
                        )}
                        {searchQuery && searchMatches.length === 0 && (
                          <span className="text-xs text-muted-foreground">No matches</span>
                        )}
                      </div>
                    </div>
                    {/* Scrollable transcript content */}
                    <div
                      ref={transcriptRef}
                      className="h-80 overflow-y-auto p-4 bg-muted/20 scrollbar-thin scrollbar-thumb-muted-foreground/30 scrollbar-track-transparent"
                      style={{
                        scrollbarWidth: 'thin',
                        scrollbarGutter: 'stable',
                      }}
                    >
                      <div className="prose prose-sm max-w-none">
                        {highlightedTranscript}
                      </div>
                    </div>
                    {/* Window footer with scroll hint */}
                    <div className="bg-muted/50 border-t px-3 py-1 text-center">
                      <span className="text-xs text-muted-foreground">
                        ↕ Scroll to read full transcript
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
