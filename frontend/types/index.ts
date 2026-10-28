export interface Order {
  id: string;
  action: "BUY" | "SELL";
  sell_token: string;
  buy_token: string;
  route_token: string | null;
  trigger_price_usd: string;
  direction: "ABOVE" | "BELOW";
  amount: string;
}

export interface MarketData {
  symbol: string;
  base: string;
  quote: string;
}