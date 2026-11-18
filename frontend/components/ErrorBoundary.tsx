"use client";
import React, { Component, ErrorInfo, ReactNode } from "react";

interface Props { children?: ReactNode; }
interface State { hasError: boolean; }

export class ErrorBoundary extends Component<Props, State> {
  public state: State = { hasError: false };

  public static getDerivedStateFromError(_: Error): State {
    return { hasError: true };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught error:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="p-4 m-4 bg-red-900/20 border border-red-500 rounded-xl text-red-400 text-sm font-mono text-center">
          Something went wrong loading this section. Please refresh the page.
        </div>
      );
    }
    return this.props.children;
  }
}