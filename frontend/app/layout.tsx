import type { Metadata } from "next";
import { Toaster } from "sonner";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cantex Terminal",
  description: "Professional Semi-Automated Trading",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-onyx-dark text-snow antialiased min-h-screen pb-16 md:pb-0">
        <AuthProvider>
          <ErrorBoundary>
            {children}
          </ErrorBoundary>

          <Toaster
            theme="dark"
            position="bottom-right"
            richColors
          />
        </AuthProvider>
      </body>
    </html>
  );
}