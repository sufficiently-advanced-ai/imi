import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// Get configuration at build time (Edge Runtime limitation)
// Check if we're in demo mode based on environment
const AUTH_MODE = process.env.NEXT_PUBLIC_AUTH_MODE || 'none';

// Custom middleware that checks authentication
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  
  // Get the session cookie
  const sessionCookie = request.cookies.get('session');
  
  // Debug logging
  console.log('[middleware]', {
    path: pathname,
    hasCookie: !!sessionCookie,
    authMode: AUTH_MODE,
    cookieValue: sessionCookie?.value ? 'present' : 'missing',
  });
  
  // List of paths that don't require authentication
  const publicPaths = [
    '/signin',
    '/(auth)/signin',
    '/auth',  // All backend auth endpoints (/auth/login, /auth/callback, /auth/me)
    '/api/auth',  // Legacy auth endpoints if any
    '/api/webhook',  // Webhooks
    '/api/health',
    '/',  // Landing page
  ];
  
  // Check if the current path is public
  const isPublicPath = publicPaths.some(path => {
    return pathname === path || 
           pathname.startsWith(path + '/') ||
           pathname.startsWith('/(auth)' + path);
  });
  
  if (isPublicPath) {
    console.log('[middleware] Public path, allowing access');
    return NextResponse.next();
  }
  
  // DEMO / NONE MODE: Set demo cookie if not present ('none' is the open
  // community mode — same flow; the backend ignores the cookie entirely)
  if (AUTH_MODE === 'demo' || AUTH_MODE === 'none') {
    if (!sessionCookie) {
      console.log('[middleware] Demo mode - setting demo cookie');
      const response = NextResponse.next();
      response.cookies.set('session', 'demo_user', {
        httpOnly: true,
        sameSite: 'lax',
        path: '/',
        secure: request.nextUrl.protocol === 'https:',
        maxAge: 86400 * 7 // 7 days
      });
      return response;
    }
    // Cookie exists, continue normally
    return NextResponse.next();
  }
  
  // PRODUCTION MODE: Check for session cookie
  if (!sessionCookie) {
    console.log('[middleware] No session cookie, redirecting to signin');
    
    // Build the signin URL
    const url = request.nextUrl.clone();
    url.pathname = '/signin';
    
    // Add the return path
    url.searchParams.set('returnTo', pathname);
    
    return NextResponse.redirect(url);
  }
  
  // Has cookie, allow access
  return NextResponse.next();
}

// Match all routes - middleware will decide what needs auth
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - _next/static (static files)  
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public folder
     * - .well-known (for various validations)
     */
    '/((?!_next/static|_next/image|favicon.ico|public|\\.well-known).*)',
  ],
};