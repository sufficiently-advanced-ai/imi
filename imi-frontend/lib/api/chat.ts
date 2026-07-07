/**
 * Chat API client for interacting with the LLM
 */
import { getApiUrl } from '@/lib/config';

export interface ChatRequest {
  question: string;
  context_files?: string[];
  prompt_type?: 'search' | 'analyze' | 'digest';
}

export interface ChatResponse {
  answer: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  confidence?: string;
  sources?: string[];
}

// Direct fetch helper to ensure relative URLs with base path
const fetchAPI = async (url: string, options?: RequestInit) => {
  console.log(`Fetching from: ${url}`);
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      throw new Error(`API request failed: ${response.statusText} (${response.status})`);
    }
    return await response.json();
  } catch (error) {
    console.error(`Error fetching from ${url}:`, error);
    throw error;
  }
};

/**
 * Send a question to the backend LLM with optional context files (legacy endpoint)
 */
export async function queryLLM(request: ChatRequest): Promise<ChatResponse> {
  try {
    // Use getApiUrl to construct proper URL with base path
    const apiUrl = getApiUrl('/query');
    return await fetchAPI(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        question: request.question,
        context_files: request.context_files || [],
        prompt_type: request.prompt_type || 'search',
      }),
    });
  } catch (error) {
    console.error('Error querying LLM:', error);
    throw error;
  }
}

export interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface StreamingChatRequest {
  query: string;
  manual_context?: string[];
  conversation_history?: ConversationMessage[];
}

export interface SSEEvent {
  type: string;
  execution_id: string;
  timestamp: string;
  [key: string]: any;
}

/**
 * Stream a chat query using SSE for real-time updates
 */
export async function streamChatQuery(
  request: StreamingChatRequest,
  onEvent: (event: SSEEvent) => void,
  onComplete: (finalAnswer: string) => void,
  onError: (error: string) => void
): Promise<void> {
  try {
    const apiUrl = getApiUrl('/chat/stream');
    console.log(`Starting streaming chat to: ${apiUrl}`);
    
    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
      body: JSON.stringify({
        query: request.query,
        manual_context: request.manual_context || null,
        conversation_history: request.conversation_history || [],
      }),
    });

    if (!response.ok) {
      throw new Error(`Streaming request failed: ${response.statusText} (${response.status})`);
    }

    if (!response.body) {
      throw new Error('No response body for streaming');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalAnswer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          console.log('Stream completed');
          break;
        }

        // Decode the chunk and add to buffer
        buffer += decoder.decode(value, { stream: true });
        
        // Process complete lines in the buffer
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer
        
        for (const line of lines) {
          if (line.trim() === '') continue;
          
          // Parse SSE format: "data: {json}"
          if (line.startsWith('data: ')) {
            try {
              const eventData = JSON.parse(line.slice(6));
              console.log('SSE Event:', eventData);
              
              // Call the event handler
              onEvent(eventData);
              
              // Collect answer from claude_response events
              if (eventData.type === 'claude_response' && eventData.content) {
                if (eventData.response_type === 'text_delta') {
                  // Incremental delta — append to running answer
                  finalAnswer += eventData.content;
                } else {
                  // Final or legacy full-replacement
                  finalAnswer = eventData.content;
                }
              }

              // Collect final answer from workflow_complete (used by demo mode)
              if (eventData.type === 'workflow_complete' && eventData.result?.answer) {
                finalAnswer = eventData.result.answer;
              }

              // Handle completion — only on final events, not incremental deltas
              if (eventData.type === 'agent_complete' ||
                  eventData.type === 'workflow_complete' ||
                  (eventData.type === 'claude_response' && eventData.is_final === true && eventData.response_type !== 'text_delta')) {
                console.log('Chat streaming completed');
                onComplete(finalAnswer);
                return;
              }
              
              // Handle errors
              if (eventData.type === 'error' || eventData.type === 'workflow_failed') {
                const errorMsg = eventData.error || 'Unknown streaming error';
                console.error('Streaming error:', errorMsg);
                onError(errorMsg);
                return;
              }
              
            } catch (parseError) {
              console.warn('Failed to parse SSE event:', line, parseError);
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
    
  } catch (error) {
    console.error('Error in streaming chat:', error);
    onError(error instanceof Error ? error.message : 'Streaming failed');
  }
}