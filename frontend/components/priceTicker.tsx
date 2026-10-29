"use client";

import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown, Activity } from "lucide-react";
import { AreaChart, Area, ResponsiveContainer, YAxis, Tooltip } from "recharts";
import { Card } from "./ui/card";

export function PriceTicker({ 
  sellToken, 
  buyToken, 
  action 
}: { 
  sellToken: string; 
  buyToken: string; 
  action: "BUY" | "SELL";
}) {
  const [price, setPrice] = useState<number | null>(null);
  const [prevPrice, setPrevPrice] = useState<number | null>(null);
  const [history, setHistory] = useState<any[]>([]);

  useEffect(() => {
    if (!sellToken || !buyToken || sellToken === buyToken) return;

    const targetToken = action === "SELL" ? buyToken : sellToken;
    const market = `${targetToken}-CC`;

    // Determine the API and WebSocket URLs dynamically
    const apiUrl = "https://smart-trade-production-44e4.up.railway.app/api";
const wsUrl = "wss://smart-trade-production-44e4.up.railway.app";

    // 1. Subscribe to the backend
    fetch(`${apiUrl}/subscribe?market=${market}`, { method: "POST" }).catch(console.error);

    // 2. Fetch 24h Graph Data
    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiUrl}/history?sell=${targetToken}&buy=CC`);
        const data = await res.json();
        setHistory(data);
      } catch (error) {
        console.error("Failed to fetch history");
      }
    };
    fetchHistory();

    // 3. Polling Fallback
    const fetchPrices = async () => {
      try {
        const res = await fetch(`${apiUrl}/price?sell=${targetToken}&buy=CC`);
        const data = await res.json();
        if (data.price && data.price !== "0") {
          const newPrice = parseFloat(data.price);
          setPrice((currentPrice) => {
            if (currentPrice !== null) setPrevPrice(currentPrice);
            return newPrice;
          });
        }
      } catch (err) {}
    };

    fetchPrices();
    const interval = setInterval(fetchPrices, 5000);

    // 4. WebSocket for instant updates
    const ws = new WebSocket(`${wsUrl}/ws/prices`);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.market === market) {
          const newPrice = parseFloat(data.price);
          setPrice((currentPrice) => {
            if (currentPrice !== null) setPrevPrice(currentPrice);
            return newPrice;
          });
        }
      } catch (err) {}
    };

    return () => {
      ws.close();
      clearInterval(interval);
    };
  }, [sellToken, buyToken, action]);

  if (!price) return null;

  const isUp = prevPrice !== null && price > prevPrice;
  const isDown = prevPrice !== null && price < prevPrice;
  const displayToken = action === "SELL" ? buyToken : sellToken;

  return (
    <Card className="bg-carbon border-jet-black mb-4 p-0 overflow-hidden flex flex-col">
      <div className="p-4 flex items-center justify-between border-b border-jet-black/50">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-jet-black rounded-lg">
            <Activity size={18} className="text-emerald animate-pulse" />
          </div>
          <div>
            <p className="text-xs text-granite font-medium uppercase tracking-wider">Live Market Price</p>
            <h3 className="text-lg font-bold text-snow font-mono">
              {displayToken} / USD
            </h3>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-2xl font-mono font-bold transition-colors duration-300 ${isUp ? 'text-emerald' : isDown ? 'text-red-400' : 'text-snow'}`}>
            ${price.toFixed(5)}
          </span>
          {isUp && <TrendingUp size={20} className="text-emerald" />}
          {isDown && <TrendingDown size={20} className="text-red-400" />}
        </div>
      </div>
      <div className="h-24 w-full bg-onyx-dark/30">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={history} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={isUp ? "#22DC95" : isDown ? "#f87171" : "#9F9DA1"} stopOpacity={0.4}/>
                <stop offset="95%" stopColor={isUp ? "#22DC95" : isDown ? "#f87171" : "#9F9DA1"} stopOpacity={0}/>
              </linearGradient>
            </defs>
            <YAxis domain={['dataMin', 'dataMax']} hide />
            <Tooltip 
              contentStyle={{ backgroundColor: '#131317', borderColor: '#252B2F', borderRadius: '8px', color: '#F9F9F9', fontSize: '12px' }}
              itemStyle={{ color: '#22DC95' }}
              labelStyle={{ display: 'none' }}
              formatter={(value: any) => [`$${Number(value).toFixed(5)}`, 'Price']}
            />
            <Area 
              type="monotone" 
              dataKey="price" 
              stroke={isUp ? "#22DC95" : isDown ? "#f87171" : "#9F9DA1"} 
              strokeWidth={2}
              fillOpacity={1} 
              fill="url(#colorPrice)" 
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}