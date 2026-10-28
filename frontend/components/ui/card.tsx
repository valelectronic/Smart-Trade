import { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`bg-onyx border border-shadow-grey rounded-xl shadow-lg p-6 ${className}`}>
      {children}
    </div>
  );
}