import { NextResponse } from "next/server";
import { getSessionToken } from "@/lib/auth-cookie";

const API_URL = process.env.CANTEX_API_URL;

export async function GET() {
  const token = await getSessionToken();

  if (!token) {
    return NextResponse.json(
      { detail: "Not signed in." },
      { status: 401 },
    );
  }

  if (!API_URL) {
    return NextResponse.json(
      { detail: "CANTEX_API_URL is not configured." },
      { status: 500 },
    );
  }

  try {
    const upstream = await fetch(`${API_URL}/api/me`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
      cache: "no-store",
    });

    const data = await upstream.json().catch(() => ({}));

    if (!upstream.ok) {
      const response = NextResponse.json(
        { detail: data.detail || "Session expired." },
        { status: upstream.status },
      );

      if (upstream.status === 401) {
        response.cookies.delete("cantex_session");
      }

      return response;
    }

    return NextResponse.json({ user: data });
  } catch {
    return NextResponse.json(
      { detail: "Could not verify your session." },
      { status: 502 },
    );
  }
}