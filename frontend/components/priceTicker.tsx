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
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");

  useEffect(() => {
    if (!sellToken || !buyToken || sellToken === buyToken) return;

    // 1. Reset state
    setPrice(null);
    setPrevPrice(null);
    setHistory([]);
    setStatus("loading");

    // 2. THE FIX: ALWAYS monitor the token you are BUYING (You Receive).
    const targetToken = buyToken.toUpperCase(); 
    
    if (targetToken === "CC") {
      setStatus("unavailable");
      return;
    }

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";
    const wsUrl = apiUrl.replace("https://", "wss://").replace("http://", "ws://").replace("/api", "");

    // 3. Try both directions to be safe (e.g., CETH-CC or CC-CETH)
    const market1 = `${targetToken}-CC`;
    const market2 = `CC-${targetToken}`;

    fetch(`${apiUrl}/subscribe?market=${market1}`, { method: "POST" }).catch(console.error);
    fetch(`${apiUrl}/subscribe?market=${market2}`, { method: "POST" }).catch(console.error);

    const fetchHistory = async () => {
      try {
        const res = await fetch(`${apiUrl}/history?sell=${targetToken}&buy=CC`);
        const data = await res.json();
        setHistory(data);
      } catch (error) {}
    };
    fetchHistory();

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
          setStatus("ready");
        }
      } catch (err) {}
    };

    fetchPrices();
    const interval = setInterval(fetchPrices, 2000);

    const ws = new WebSocket(`${wsUrl}/ws/prices`);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Check if the WebSocket message matches either market format
        if (data.market === market1 || data.market === market2) {
          const newPrice = parseFloat(data.price);
          setPrice((currentPrice) => {
            if (currentPrice !== null) setPrevPrice(currentPrice);
            return newPrice;
          });
          setStatus("ready");
        }
      } catch (err) {}
    };

    const timeoutId = setTimeout(() => {
      setStatus((currentStatus) => {
        if (currentStatus === "loading") return "unavailable";
        return currentStatus;
      });
    }, 8000);

    return () => {
      ws.close();
      clearInterval(interval);
      clearTimeout(timeoutId);
    };
  }, [sellToken, buyToken, action]);

  const isUp = prevPrice !== null && price !== null && price > prevPrice;
  const isDown = prevPrice !== null && price !== null && price < prevPrice;
  
  // Display the token we are monitoring (the BUY token)
  const displayToken = buyToken.toUpperCase();

  return (
    <Card className="bg-carbon border-jet-black mb-4 p-0 overflow-hidden flex flex-col">
      <div className="p-4 flex items-center justify-between border-b border-jet-black/50 gap-2">
        
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2 bg-jet-black rounded-lg shrink-0">
            <Activity size={18} className={status === "loading" ? "text-emerald animate-pulse" : "text-granite"} />
          </div>
          <div className="min-w-0">
            <p className="text-xs text-granite font-medium uppercase tracking-wider">Live Market Price</p>
            <h3 className="text-lg font-bold text-snow font-mono truncate">
              {displayToken} / USD
            </h3>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {status === "loading" && (
            <span className="text-sm text-granite animate-pulse">Fetching...</span>
          )}
          {status === "unavailable" && (
            <span className="text-xs text-red-400 font-medium bg-red-500/10 px-2 py-1 rounded">Price Unavailable</span>
          )}
          {status === "ready" && (
            <>
              <span className={`text-xl md:text-2xl font-mono font-bold transition-colors duration-300 ${isUp ? 'text-emerald' : isDown ? 'text-red-400' : 'text-snow'}`}>
                ${price ? price.toFixed(5) : "0.00000"}
              </span>
              {isUp && <TrendingUp size={20} className="text-emerald" />}
              {isDown && <TrendingDown size={20} className="text-red-400" />}
            </>
          )}
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