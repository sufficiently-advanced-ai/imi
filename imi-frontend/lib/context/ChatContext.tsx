'use client';

import { createContext, useContext, useState, useRef, ReactNode, useCallback } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { queryLLM, streamChatQuery, SSEEvent, ConversationMessage } from '../api/chat';
import { useKnowledge } from './KnowledgeContext';

// Types for chat messages
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  contextFiles?: string[];
  isPending?: boolean;
  error?: string;
  confidence?: string;
  sources?: string[];
  streamingEvents?: SSEEvent[];
  streamingStatus?: 'starting' | 'thinking' | 'using_tools' | 'completing' | 'complete' | 'error';
}

// Chat context interface
interface ChatContextType {
  // State
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  useStreaming: boolean;
  
  // Actions
  sendMessage: (content: string) => Promise<void>;
  clearChat: () => void;
  toggleStreaming: () => void;
}

// Create the context with default values
const ChatContext = createContext<ChatContextType>({
  messages: [],
  isLoading: false,
  error: null,
  useStreaming: true,
  sendMessage: async () => {},
  clearChat: () => {},
  toggleStreaming: () => {},
});

// Provider component
export function ChatProvider({ children }: { children: ReactNode }) {
  // State
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: uuidv4(),
      role: 'assistant',
      content: 'How can I help you today? I can search your knowledge base, summarize meetings, find connections between people and topics, and more.',
      timestamp: new Date().toISOString(),
    },
  ]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [useStreaming, setUseStreaming] = useState<boolean>(true);

  // Ref to always have latest messages (avoids stale closure in useCallback)
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  
  // Get selected files from the Knowledge context
  const { selectedFiles } = useKnowledge();

  // Send a message to the LLM
  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;
    
    // Create user message
    const userMessage: ChatMessage = {
      id: uuidv4(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
      contextFiles: selectedFiles.length > 0 ? [...selectedFiles] : undefined,
    };
    
    // Add user message to the chat
    setMessages(prev => [...prev, userMessage]);
    
    // Set loading state
    setIsLoading(true);
    setError(null);
    
    // Create pending assistant message
    const pendingId = uuidv4();
    const pendingMessage: ChatMessage = {
      id: pendingId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      isPending: true,
      streamingEvents: [],
      streamingStatus: 'starting',
    };
    
    // Add pending message to the chat
    setMessages(prev => [...prev, pendingMessage]);
    
    if (useStreaming) {
      // Use streaming endpoint
      try {
        // Build conversation history from current messages via ref (avoids stale closure)
        const conversationHistory: ConversationMessage[] = messagesRef.current
          .filter(msg => (msg.role === 'user' || msg.role === 'assistant') && !msg.isPending && msg.content)
          .map(msg => ({ role: msg.role as 'user' | 'assistant', content: msg.content }));

        await streamChatQuery(
          {
            query: content,
            manual_context: selectedFiles.length > 0 ? selectedFiles : undefined,
            conversation_history: conversationHistory.length > 0 ? conversationHistory : undefined,
          },
          // onEvent
          (event: SSEEvent) => {
            console.log('Received SSE event:', event);
            
            // Update the pending message with streaming events
            setMessages(prev => 
              prev.map(msg => {
                if (msg.id !== pendingId) return msg;
                
                const updatedEvents = [...(msg.streamingEvents || []), event];
                let newStatus = msg.streamingStatus;
                let newContent = msg.content;
                
                // Update status based on event type
                switch (event.type) {
                  case 'agent_start':
                    newStatus = 'starting';
                    newContent = '🚀 Starting to process your request...';
                    break;
                  case 'claude_thinking':
                    newStatus = 'thinking';
                    newContent = '🤔 Analyzing and planning next steps...';
                    break;
                  case 'tool_start':
                    newStatus = 'using_tools';
                    newContent = `🔧 Using ${event.tool_name}...`;
                    break;
                  case 'tool_complete':
                    newContent = `✅ Completed ${event.tool_name} (${event.duration?.toFixed(2)}s)`;
                    break;
                  case 'claude_response':
                    if (event.response_type === 'text_delta' && event.content) {
                      // Incremental streaming: append delta text
                      newStatus = 'completing';
                      // If previous content was a status message (starts with emoji), reset it
                      const isStatusMsg = msg.content.match(/^[\u{1F680}\u{1F914}\u{1F527}\u{2705}]/u);
                      newContent = isStatusMsg ? event.content : msg.content + event.content;
                    } else if (event.is_final && event.content) {
                      // Final answer (fallback for non-streaming path)
                      newStatus = 'completing';
                      newContent = event.content;
                    }
                    break;
                }
                
                return {
                  ...msg,
                  content: newContent,
                  streamingEvents: updatedEvents,
                  streamingStatus: newStatus,
                };
              })
            );
          },
          // onComplete
          (finalAnswer: string) => {
            console.log('Streaming completed with answer:', finalAnswer);
            setMessages(prev =>
              prev.map(msg => {
                if (msg.id !== pendingId) return msg;
                // If we already have streamed content, keep it.
                // Only use finalAnswer as a fallback for non-streaming paths.
                const hasStreamedContent = msg.content && msg.streamingStatus === 'completing';
                return {
                  ...msg,
                  content: hasStreamedContent ? msg.content : (finalAnswer || msg.content),
                  isPending: false,
                  streamingStatus: 'complete'
                };
              })
            );
            setIsLoading(false);
          },
          // onError
          (errorMessage: string) => {
            console.error('Streaming error:', errorMessage);
            setError(errorMessage);
            setMessages(prev => 
              prev.map(msg => 
                msg.id === pendingId 
                  ? { 
                      ...msg, 
                      content: 'Sorry, I encountered an error during streaming. Please try again.', 
                      isPending: false, 
                      error: errorMessage,
                      streamingStatus: 'error'
                    } 
                  : msg
              )
            );
            setIsLoading(false);
          }
        );
      } catch (err) {
        console.error('Error starting streaming:', err);
        const errorMessage = err instanceof Error ? err.message : 'Failed to start streaming';
        setError(errorMessage);
        
        setMessages(prev => 
          prev.map(msg => 
            msg.id === pendingId 
              ? { 
                  ...msg, 
                  content: 'Sorry, I couldn\'t start the streaming request. Please try again.', 
                  isPending: false, 
                  error: errorMessage,
                  streamingStatus: 'error'
                } 
              : msg
          )
        );
        setIsLoading(false);
      }
      return;
    }
    
    // Use legacy endpoint
    try {
      // Query the LLM
      const response = await queryLLM({
        question: content,
        context_files: selectedFiles.length > 0 ? selectedFiles : undefined,
        prompt_type: 'search',
      });

      // Extract answer and parse if needed
      console.log('[ChatContext] Raw API response:', response);
      let answer = response.answer;
      let confidence = response.confidence;
      let sources = response.sources;

      // Process answer to ensure it's properly formatted
      if (typeof answer === 'object' && answer !== null) {
        // If answer is an object directly, extract the text content
        const answerObj = answer as Record<string, unknown>;
        if ('answer' in answerObj && answerObj.answer) {
          answer = String(answerObj.answer);
        } else if ('content' in answerObj && answerObj.content) {
          answer = String(answerObj.content);
        } else if ('text' in answerObj && answerObj.text) {
          answer = String(answerObj.text);
        } else if ('message' in answerObj && answerObj.message) {
          answer = String(answerObj.message);
        } else {
          // Last resort - stringify but in a readable way
          answer = JSON.stringify(answer, null, 2);
        }
      }

      // Try to parse the answer if it appears to be JSON (both object and array formats)
      if (typeof answer === 'string' &&
         ((answer.trim().startsWith('{') && answer.trim().endsWith('}')) ||
          (answer.trim().startsWith('[') && answer.trim().endsWith(']')))) {
        try {
          const parsedAnswer = JSON.parse(answer);

          // Extract values from parsed JSON if they exist
          if (parsedAnswer.answer) {
            answer = parsedAnswer.answer;
          } else if (parsedAnswer.content) {
            answer = parsedAnswer.content;
          } else if (parsedAnswer.text) {
            answer = parsedAnswer.text;
          } else if (parsedAnswer.message) {
            answer = parsedAnswer.message;
          }

          if (parsedAnswer.confidence && !confidence) {
            confidence = typeof parsedAnswer.confidence === 'number'
              ? parsedAnswer.confidence.toString()
              : parsedAnswer.confidence;
          }

          if (parsedAnswer.sources && !sources) {
            sources = Array.isArray(parsedAnswer.sources) ? parsedAnswer.sources : [parsedAnswer.sources];
          }
        } catch (e) {
          // If parsing fails, use the original answer
          console.warn('Failed to parse answer as JSON:', e);
        }
      }

      // Ensure answer is a string
      answer = String(answer);
      
      // Check if answer is empty or just whitespace
      if (!answer || answer.trim() === '' || answer === 'undefined' || answer === 'null') {
        console.error('[ChatContext] Empty or invalid answer received:', answer);
        answer = 'I apologize, but I couldn\'t generate a response. The server returned an empty answer. Please check the logs and try again.';
      }
      
      console.log('[ChatContext] Final answer:', answer);
      console.log('[ChatContext] Confidence:', confidence);

      // Replace pending message with actual response
      const assistantMessage: ChatMessage = {
        id: pendingId,
        role: 'assistant',
        content: answer,
        timestamp: new Date().toISOString(),
        isPending: false,
        confidence: confidence,
        sources: sources,
      };
      
      setMessages(prev => 
        prev.map(msg => msg.id === pendingId ? assistantMessage : msg)
      );
    } catch (err) {
      console.error('Error sending message:', err);
      
      // Update pending message with error
      const errorMessage = err instanceof Error ? err.message : 'Failed to send message';
      setError(errorMessage);
      
      setMessages(prev => 
        prev.map(msg => 
          msg.id === pendingId 
            ? { 
                ...msg, 
                content: 'Sorry, I encountered an error. Please try again.', 
                isPending: false, 
                error: errorMessage 
              } 
            : msg
        )
      );
    } finally {
      setIsLoading(false);
    }
  }, [selectedFiles, useStreaming]);

  // Clear the chat history
  const clearChat = useCallback(() => {
    setMessages([
      {
        id: uuidv4(),
        role: 'assistant',
        content: 'Chat history cleared. How can I help you today?',
        timestamp: new Date().toISOString(),
      },
    ]);
    setError(null);
  }, []);

  // Toggle streaming mode
  const toggleStreaming = useCallback(() => {
    setUseStreaming(prev => !prev);
  }, []);

  return (
    <ChatContext.Provider
      value={{
        messages,
        isLoading,
        error,
        useStreaming,
        sendMessage,
        clearChat,
        toggleStreaming,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

// Custom hook to use the chat context
export const useChat = () => useContext(ChatContext);