'use client';

import { useState, useRef, useEffect } from 'react';
import { useChat } from '@/lib/context';
import { Button } from '@/components/ui/button';
import ChatMessage from './ChatMessage';
import { Send, Sparkles, FileText, Users, TrendingUp, Loader2 } from 'lucide-react';

const SUGGESTED_PROMPTS = [
  {
    icon: FileText,
    title: "Summarize recent meetings",
    prompt: "Can you summarize the key points and action items from my recent meetings?"
  },
  {
    icon: Users,
    title: "Find related people",
    prompt: "Who are the key people connected to [project name]?"
  },
  {
    icon: TrendingUp,
    title: "Identify patterns",
    prompt: "What patterns or trends do you see across my meeting notes?"
  }
];

function EmptyState({ onPromptSelect }: { onPromptSelect: (prompt: string) => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
      {/* Logo/Icon */}
      <div className="mb-6">
        <div className="h-16 w-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20 flex items-center justify-center">
          <Sparkles className="h-8 w-8 text-primary" />
        </div>
      </div>

      {/* Welcome text */}
      <h1 className="text-2xl font-semibold text-foreground mb-2">
        How can I help you today?
      </h1>
      <p className="text-muted-foreground max-w-md mb-8">
        I can help you search your knowledge base, analyze meeting notes, find connections between people and topics, and more.
      </p>

      {/* Suggested prompts */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 w-full max-w-2xl">
        {SUGGESTED_PROMPTS.map((item, index) => {
          const Icon = item.icon;
          return (
            <button
              key={index}
              onClick={() => onPromptSelect(item.prompt)}
              className="group flex flex-col items-start gap-2 p-4 rounded-xl border border-border bg-card hover:bg-accent hover:border-primary/30 transition-all duration-200 text-left"
            >
              <div className="p-2 rounded-lg bg-primary/10 text-primary group-hover:bg-primary/20 transition-colors">
                <Icon className="h-4 w-4" />
              </div>
              <span className="text-sm font-medium text-foreground">{item.title}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-3 p-4">
      <div className="h-8 w-8 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center">
        <Sparkles className="h-4 w-4 text-primary" />
      </div>
      <div className="flex items-center gap-1 px-4 py-3 rounded-2xl bg-muted border border-border">
        <div className="flex gap-1">
          <span className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        <span className="ml-2 text-sm text-muted-foreground">Thinking...</span>
      </div>
    </div>
  );
}

export default function ChatInterface() {
  const { messages, sendMessage, isLoading, useStreaming, toggleStreaming } = useChat();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Function to handle sending a message
  const handleSendMessage = async () => {
    if (!input.trim() || isLoading) return;
    const message = input;
    setInput('');
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    await sendMessage(message);
  };

  // Function to handle pressing Enter to send
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Auto-resize textarea
  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    // Auto-resize
    const textarea = e.target;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  };

  // Handle suggested prompt selection
  const handlePromptSelect = (prompt: string) => {
    setInput(prompt);
    textareaRef.current?.focus();
  };

  // Scroll to bottom when messages change
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const hasMessages = messages.length > 0;

  return (
    <div className="flex flex-col h-full max-w-4xl mx-auto w-full">
      {/* Messages area or empty state */}
      {hasMessages ? (
        <div className="flex-1 overflow-y-auto chat-scrollbar">
          <div className="p-4 space-y-1">
            {messages.map(message => (
              <ChatMessage key={message.id} message={message} />
            ))}
            {isLoading && messages[messages.length - 1]?.role === 'user' && (
              <TypingIndicator />
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>
      ) : (
        <EmptyState onPromptSelect={handlePromptSelect} />
      )}

      {/* Input area */}
      <div className="border-t border-border p-4">
        <div className="relative">
          <div className="flex items-end gap-2 p-2 rounded-2xl border border-border bg-card focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/20 transition-all">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              placeholder="Ask me anything about your knowledge base..."
              className="flex-1 bg-transparent text-foreground placeholder:text-muted-foreground resize-none focus:outline-none min-h-[24px] max-h-[200px] py-2 px-2"
              rows={1}
              disabled={isLoading}
            />
            <Button
              onClick={handleSendMessage}
              disabled={isLoading || !input.trim()}
              size="icon"
              className="h-9 w-9 rounded-xl flex-shrink-0 bg-primary hover:bg-primary/90 disabled:opacity-50"
              aria-label="Send message"
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>

          {/* Footer with keyboard hint and streaming toggle */}
          <div className="flex items-center justify-between mt-2 px-2">
            <span className="text-xs text-muted-foreground">
              Press <kbd className="px-1.5 py-0.5 text-[10px] font-mono bg-muted rounded border border-border">Enter</kbd> to send, <kbd className="px-1.5 py-0.5 text-[10px] font-mono bg-muted rounded border border-border">Shift + Enter</kbd> for new line
            </span>
            <button
              onClick={toggleStreaming}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5"
            >
              <span className={`w-1.5 h-1.5 rounded-full ${useStreaming ? 'bg-green-500' : 'bg-orange-500'}`} />
              {useStreaming ? 'Live response' : 'Full response'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
