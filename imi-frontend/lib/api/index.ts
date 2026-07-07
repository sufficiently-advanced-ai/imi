/**
 * API client utilities
 */

export * from "./knowledge";
export * from "./chat";
export * from "./command";
export * from "./entities";
export * from "./users";
export * from "./meetings";
export * from "./signals";
export * from "./captures";
export * from "./decisions";

/**
 * Base fetch helper for API calls
 */
export const fetcher = async (url: string, options?: RequestInit) => {
  // Import the config (dynamic import to avoid circular dependencies)
  const { getApiUrl } = await import("../config");

  if (process.env.NODE_ENV === "development") {
    console.log("[Fetcher] Input URL:", url);
  }

  // Use the centralized URL construction
  const fullUrl = getApiUrl(url);

  if (process.env.NODE_ENV === "development") {
    console.log("[Fetcher] Full URL after getApiUrl:", fullUrl);
    console.log(
      "[Fetcher] Window location:",
      typeof window !== "undefined" ? window.location.pathname : "SSR",
    );
  }

  // Add credentials: 'include' to all requests for auth
  const enhancedOptions = {
    ...options,
    credentials: "include" as RequestCredentials,
  };

  try {
    const response = await fetch(fullUrl, enhancedOptions);

    // Handle 401 — session expired and could not be refreshed
    if (response.status === 401) {
      console.warn("[Fetcher] 401 Unauthorized — redirecting to sign-in");
      if (typeof window !== "undefined") {
        window.location.href = "/signin";
      }
      // Throw so callers don't try to process the response
      throw Object.assign(new Error("Session expired"), {
        status: 401,
        data: { detail: "Not authenticated" },
      });
    }

    if (!response.ok) {
      let errorMessage = `API request failed: ${response.status}`;
      try {
        const errorData = await response.json();

        // Include error details in the message if available
        if (errorData.detail) {
          errorMessage += ` - ${errorData.detail}`;
        }

        const error = new Error(errorMessage);
        throw Object.assign(error, {
          status: response.status,
          data: errorData,
        });
      } catch {
        // If we can't parse the error response as JSON (jsonError variable unused)
        // If we can't parse the error response as JSON
        const error = new Error(errorMessage);
        throw Object.assign(error, {
          status: response.status,
          data: { message: "Could not parse error response" },
        });
      }
    }

    // Handle 204 No Content responses
    if (response.status === 204) {
      return null;
    }

    return response.json();
  } catch (err) {
    // Don't wrap 401 errors — they already have the right shape
    if (err instanceof Error && (err as any).status === 401) {
      throw err;
    }
    // Re-throw with additional context
    const e = err instanceof Error ? err : new Error(String(err));
    throw Object.assign(
      new Error(`Network error when contacting API: ${e.message}`),
      {
        originalError: err,
        url: fullUrl,
      },
    );
  }
};

// Export fetcher as apiClient for convenience
export const apiClient = fetcher;
