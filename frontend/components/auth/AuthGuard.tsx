"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuth } from "./AuthProvider";

export function AuthGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center bg-onyx-dark">
        <div className="flex flex-col items-center gap-3 text-granite">
          <Loader2 className="h-7 w-7 animate-spin text-emerald" />
          <span className="text-sm">
            Verifying your session…
          </span>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}