import { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "danger" | "outline";
  isLoading?: boolean;
}

export function Button({ children, variant = "primary", isLoading, className = "", ...props }: ButtonProps) {
  const baseStyle = "px-4 py-2 rounded-lg font-medium transition-all flex items-center justify-center gap-2 disabled:opacity-50 text-sm";
  
  const variants = {
    primary: "bg-emerald hover:bg-emerald/90 text-onyx-dark shadow-[0_0_15px_rgba(34,220,149,0.2)]",
    danger: "bg-red-500/10 text-red-500 hover:bg-red-500/20",
    outline: "border border-jet-black hover:bg-jet-black text-snow",
  };

  return (
    <button className={`${baseStyle} ${variants[variant]} ${className}`} disabled={isLoading || props.disabled} {...props}>
      {isLoading ? "Processing..." : children}
    </button>
  );
}