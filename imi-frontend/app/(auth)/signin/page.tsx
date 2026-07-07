'use client';

import { Button } from '@/components/ui/button';

export default function SignInPage() {
  // Use relative URL for login - will work with any base path
  const loginUrl = '/auth/login';
  
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-8">
        <div>
          <h2 className="mt-6 text-center text-3xl font-extrabold text-gray-900">
            Sign in to imi
          </h2>
        </div>
        <div className="mt-8 space-y-6">
          <a href={loginUrl} className="block">
            <Button
              className="w-full"
              size="lg"
              type="button"
              asChild
            >
              <span>Log in to Sufficiently Advanced AI</span>
            </Button>
          </a>
        </div>
      </div>
    </div>
  );
}