"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Trash2, ArrowRight, Loader2 } from "lucide-react";
import { getOrders, deleteOrder } from "@/lib/api";
import { Order } from "@/types";
import { Card } from "./ui/card";
import { Button } from "./ui/Button";

export function OrderTable({ refreshTrigger }: { refreshTrigger: number }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fetchOrders = async () => {
    setIsLoading(true);
    try {
      const data = await getOrders();
      setOrders(data);
    } catch (error) {
      toast.error("Failed to load orders");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, [refreshTrigger]);

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await deleteOrder(id);
      toast.success("Order deleted");
      fetchOrders();
    } catch (error) {
      toast.error("Failed to delete order");
    } finally {
      setDeletingId(null);
    }
  };

  if (isLoading) {
    return (
      <Card className="flex items-center justify-center h-64 bg-onyx border-shadow-grey">
        <Loader2 className="w-8 h-8 animate-spin text-emerald" />
      </Card>
    );
  }

  if (orders.length === 0) {
    return (
      <Card className="flex flex-col items-center justify-center h-64 text-granite bg-onyx border-shadow-grey">
        <p>No active strategies.</p>
        <p className="text-sm mt-2">Create one using the form.</p>
      </Card>
    );
  }

  return (
    <Card className="p-0 overflow-hidden bg-onyx border-shadow-grey">
      {/* Desktop Table */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-shadow-grey bg-jet-black text-xs uppercase tracking-wider text-granite">
              <th className="p-4 font-medium">Trade Pair</th>
              <th className="p-4 font-medium">Action</th>
              <th className="p-4 font-medium">Trigger</th>
              <th className="p-4 font-medium">Amount</th>
              <th className="p-4 font-medium text-right">Manage</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-shadow-grey/50">
            {orders.map((order) => (
              <tr key={order.id} className="hover:bg-jet-black/50 transition-colors">
                <td className="p-4">
                  <div className="flex items-center gap-2 font-mono text-sm text-snow">
                    <span>{order.sell_token}</span>
                    <ArrowRight size={14} className="text-granite" />
                    <span>{order.buy_token}</span>
                  </div>
                  {order.route_token && (
                    <span className="text-[10px] text-amber-500 mt-1 block">
                      * Routed via CC
                    </span>
                  )}
                </td>
                <td className="p-4">
                  <span className={`inline-block px-2.5 py-1 rounded-md text-xs font-bold ${order.action === "BUY" ? "bg-emerald/10 text-emerald" : "bg-red-500/10 text-red-400"}`}>
                    {order.action}
                  </span>
                </td>
                <td className="p-4 text-granite font-mono text-sm">
                  {order.direction === "ABOVE" ? ">" : "<"} ${order.trigger_price_usd}
                </td>
                <td className="p-4 text-granite font-mono text-sm">
                  {order.amount} {order.sell_token}
                </td>
                <td className="p-4 text-right">
                  <Button 
                    variant="danger" 
                    onClick={() => handleDelete(order.id)}
                    disabled={deletingId === order.id}
                    className="px-3 py-1.5"
                  >
                    {deletingId === order.id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Cards */}
      <div className="md:hidden flex flex-col divide-y divide-shadow-grey">
        {orders.map((order) => (
          <div key={order.id} className="p-4 flex flex-col gap-3">
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-2 font-mono text-sm text-snow">
                <span>{order.sell_token}</span>
                <ArrowRight size={14} className="text-granite" />
                <span>{order.buy_token}</span>
              </div>
              <span className={`px-2 py-1 rounded-md text-xs font-bold ${order.action === "BUY" ? "bg-emerald/10 text-emerald" : "bg-red-500/10 text-red-400"}`}>
                {order.action}
              </span>
            </div>
            <div className="text-sm text-granite flex justify-between font-mono bg-jet-black p-2 rounded-md">
              <span>Trigger: {order.direction === "ABOVE" ? ">" : "<"} ${order.trigger_price_usd}</span>
              <span>Amt: {order.amount}</span>
            </div>
            <Button 
              variant="danger" 
              onClick={() => handleDelete(order.id)}
              disabled={deletingId === order.id}
              className="w-full mt-1"
            >
              {deletingId === order.id ? "Deleting..." : "Delete Strategy"}
            </Button>
          </div>
        ))}
      </div>
    </Card>
  );
}