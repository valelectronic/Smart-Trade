import { NextResponse } from "next/server";
import { AUTH_COOKIE } from "@/lib/auth-cookie";

export async function POST() {
  const response = NextResponse.json({ status: "success" });

  response.cookies.set(AUTH_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });

  return response;
}