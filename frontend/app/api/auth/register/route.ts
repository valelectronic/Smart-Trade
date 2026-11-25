import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, AUTH_MAX_AGE } from "@/lib/auth-cookie";
import type { AuthResponse } from "@/lib/auth-types";

const API_URL = process.env.CANTEX_API_URL;

export async function POST(request: NextRequest) {
  if (!API_URL) {
    return NextResponse.json(
      { detail: "CANTEX_API_URL is not configured." },
      { status: 500 },
    );
  }

  try {
    const body = await request.json();

    if (
      typeof body.username !== "string" ||
      typeof body.password !== "string"
    ) {
      return NextResponse.json(
        { detail: "Username and password are required." },
        { status: 400 },
      );
    }

    if (body.password.length < 6) {
      return NextResponse.json(
        { detail: "Password must contain at least 6 characters." },
        { status: 400 },
      );
    }

    const upstream = await fetch(`${API_URL}/api/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });

    const data = await upstream.json().catch(() => ({}));

    if (!upstream.ok) {
      return NextResponse.json(
        {
          detail:
            data.detail ||
            data.message ||
            "Unable to create your account.",
        },
        { status: upstream.status },
      );
    }

    const auth = data as AuthResponse;

    if (!auth.token || !auth.user) {
      return NextResponse.json(
        {
          detail:
            "The authentication server returned an invalid response.",
        },
        { status: 502 },
      );
    }

    const response = NextResponse.json(
      { user: auth.user },
      { status: 201 },
    );

    response.cookies.set(AUTH_COOKIE, auth.token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: Math.min(
        Number(auth.expires_in) || AUTH_MAX_AGE,
        AUTH_MAX_AGE,
      ),
    });

    return response;
  } catch {
    return NextResponse.json(
      { detail: "Could not connect to the registration service." },
      { status: 502 },
    );
  }
}