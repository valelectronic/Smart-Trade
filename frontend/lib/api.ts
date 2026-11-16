import { Order, MarketData } from "@/types";

// Base URL WITHOUT /api at the end
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function getBalances(): Promise<Record<string, string>> {
  // We append /api/portfolio-value here
  const res = await fetch(`${API_URL}/api/portfolio-value`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch balances");
  const data = await res.json();
  
  if (data.status === "success" && data.breakdown) {
    return data.breakdown;
  }
  return {};
}
// ... (leave the legacy stub functions exactly as they are) ...

// Legacy functions (kept for type compatibility, but unused)
export async function getOrders(): Promise<Order[]> {
  throw new Error("Buy/Sell feature is disabled in Lean Mode");
}

export async function createOrder(order: Partial<Order>): Promise<Order> {
  throw new Error("Buy/Sell feature is disabled in Lean Mode");
}

export async function deleteOrder(id: string): Promise<void> {
  throw new Error("Buy/Sell feature is disabled in Lean Mode");
}

export async function getMarkets(): Promise<MarketData[]> {
  return [];
}

export async function getLivePrice(sell: string, buy: string): Promise<{ price: string }> {
  return { price: "0" };
}