import { Order, MarketData } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";

export async function getOrders(): Promise<Order[]> {
  const res = await fetch(`${API_URL}/orders`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch orders");
  return res.json();
}

export async function createOrder(order: Partial<Order>): Promise<Order> {
  const res = await fetch(`${API_URL}/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(order),
  });
  
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "Failed to create order");
  }
  return res.json();
}

export async function deleteOrder(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/orders/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete order");
}

export async function getMarkets(): Promise<MarketData[]> {
  const res = await fetch(`${API_URL}/markets`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch markets");
  return res.json();
}

export async function getBalances(): Promise<Record<string, string>> {
  const res = await fetch(`${API_URL}/balances`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch balances");
  return res.json();
}

export async function getLivePrice(sell: string, buy: string): Promise<{ price: string }> {
  const res = await fetch(`${API_URL}/price?sell=${sell}&buy=${buy}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch price");
  return res.json();
}