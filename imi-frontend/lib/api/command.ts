/**
 * API client for Command Center functionality
 */
import { getApiUrl } from '@/lib/config';

export interface ServiceConfig {
  name: string;
  status: 'unknown' | 'connected' | 'error' | 'warning' | 'not_configured';
  error_message?: string;
}

export interface ClaudeConfig extends ServiceConfig {
  name: 'claude';
  api_key: string;
  model: string;
}

export interface GitHubConfig extends ServiceConfig {
  name: 'github';
  token: string;
  repo: string;
  webhook_secret?: string;
}

export interface SystemConfig {
  claude: ClaudeConfig;
  github: GitHubConfig;
}

export interface ConnectionTestRequest {
  service: 'claude' | 'github';
}

export interface ConfigUpdateRequest {
  claude?: Partial<ClaudeConfig>;
  github?: Partial<GitHubConfig>;
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
 * Get the current system configuration
 */
export const getConfig = async (): Promise<SystemConfig> => {
  return fetchAPI(getApiUrl('/command/config'));
};

/**
 * Update the system configuration
 */
export const updateConfig = async (config: ConfigUpdateRequest): Promise<SystemConfig> => {
  return fetchAPI(getApiUrl('/command/config'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(config),
  });
};

/**
 * Get the status of all connections
 */
export const getStatus = async (): Promise<Record<string, ServiceConfig>> => {
  return fetchAPI(getApiUrl('/command/status'));
};

/**
 * Test a specific connection
 */
export const testConnection = async (service: string): Promise<ServiceConfig> => {
  return fetchAPI(getApiUrl('/command/test-connection'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ service }),
  });
};

/**
 * Format the status for display
 */
export const formatStatus = (status: string): string => {
  switch (status) {
    case 'connected':
      return 'Connected';
    case 'error':
      return 'Error';
    case 'warning':
      return 'Warning';
    case 'not_configured':
      return 'Not Configured';
    case 'unknown':
    default:
      return 'Unknown';
  }
};

/**
 * Get the status color for the given status
 */
export const getStatusColor = (status: string): string => {
  switch (status) {
    case 'connected':
      return 'bg-green-500';
    case 'error':
      return 'bg-red-500';
    case 'warning':
      return 'bg-yellow-500';
    case 'not_configured':
      return 'bg-gray-400';
    case 'unknown':
    default:
      return 'bg-gray-300';
  }
};