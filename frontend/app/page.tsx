"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { getRefreshToken } from "@/lib/auth";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getRefreshToken() ? "/invoices" : "/login");
  }, [router]);

  return null;
}
