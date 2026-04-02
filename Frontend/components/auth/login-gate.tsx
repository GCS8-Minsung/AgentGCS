"use client";

import { useEffect, useMemo, useState } from "react";
import { FlaskConical, KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";

const DEFAULT_CLIENT_ID =
  "513803184584-7sb5sp4qv68a534kvd0u3inp0ruf021r.apps.googleusercontent.com";
const AUTH_STORAGE_KEY = "agentgcs_auth_session";

type AuthSession = {
  userId: string;
  email: string | null;
  fullName: string | null;
  avatarUrl: string | null;
  provider: "google" | "dev";
};

type Props = {
  onAuthenticated: (session: AuthSession) => void;
};

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
          prompt: () => void;
        };
      };
    };
  }
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const decoded = atob(payload.padEnd(payload.length + ((4 - (payload.length % 4)) % 4), "="));
    return JSON.parse(decoded) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function deterministicUuidFromString(seed: string): string {
  const bytes = new Uint8Array(16);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < seed.length; i += 1) {
    const code = seed.charCodeAt(i);
    h1 ^= code;
    h1 = Math.imul(h1, 0x01000193);
    h2 ^= code + i;
    h2 = Math.imul(h2, 0x01000195);
  }
  for (let i = 0; i < 16; i += 1) {
    const source = i % 2 === 0 ? h1 : h2;
    bytes[i] = (source >> ((i % 4) * 8)) & 0xff;
    h1 = Math.imul(h1 ^ (i + 17), 0x01000193);
    h2 = Math.imul(h2 ^ (i + 31), 0x01000195);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(
    16,
    20
  )}-${hex.slice(20, 32)}`;
}

export function LoginGate({ onAuthenticated }: Props) {
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const clientId = useMemo(
    () => process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? DEFAULT_CLIENT_ID,
    []
  );

  useEffect(() => {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as AuthSession;
        if (parsed.userId) {
          onAuthenticated(parsed);
          return;
        }
      } catch {
        localStorage.removeItem(AUTH_STORAGE_KEY);
      }
    }
    setLoading(false);
  }, [onAuthenticated]);

  useEffect(() => {
    if (loading) return;
    if (window.google?.accounts?.id) {
      setReady(true);
      return;
    }

    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => setReady(true);
    document.head.appendChild(script);
    return () => {
      script.remove();
    };
  }, [loading]);

  useEffect(() => {
    if (!ready || loading) return;
    const container = document.getElementById("google-login-button");
    if (!container || !window.google?.accounts?.id) return;
    container.innerHTML = "";

    window.google.accounts.id.initialize({
      client_id: clientId,
      callback: (response: { credential?: string }) => {
        if (!response.credential) return;
        const payload = decodeJwtPayload(response.credential);
        const sub = String(payload?.sub ?? crypto.randomUUID());
        const session: AuthSession = {
          userId: deterministicUuidFromString(sub),
          email: typeof payload?.email === "string" ? payload.email : null,
          fullName: typeof payload?.name === "string" ? payload.name : null,
          avatarUrl: typeof payload?.picture === "string" ? payload.picture : null,
          provider: "google"
        };
        localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
        onAuthenticated(session);
      }
    });

    window.google.accounts.id.renderButton(container, {
      theme: "filled_blue",
      size: "large",
      shape: "pill",
      locale: "ko",
      width: 280
    });
    window.google.accounts.id.prompt();
  }, [ready, loading, clientId, onAuthenticated]);

  if (loading) {
    return (
      <main className="flex h-screen w-full items-center justify-center">
        <p className="text-sm text-orange-900/70">로그인 상태 확인 중...</p>
      </main>
    );
  }

  return (
    <main className="flex h-screen w-full items-center justify-center px-4">
      <Card className="w-full max-w-xl space-y-5 p-6 md:p-8">
        <div className="space-y-2">
          <CardTitle className="text-2xl md:text-3xl">AgentGCS 시작</CardTitle>
          <CardDescription>
            Google 로그인 후 사용자 키를 연결하면 워크스페이스를 사용할 수 있습니다.
          </CardDescription>
        </div>

        <div className="rounded-2xl border border-white/80 bg-white/55 p-4">
          <p className="mb-3 text-sm font-semibold text-gray-800">Google OAuth 2.0 로그인</p>
          <div id="google-login-button" className="min-h-10" />
        </div>

        <div className="rounded-2xl border border-amber-200/70 bg-amber-50/70 p-4 text-sm text-amber-900/85">
          <p className="font-semibold">최초 접속 안내</p>
          <p className="mt-1">
            Google 로그인 후 `api.1000.school` 토큰 입력을 요청합니다. 입력된 토큰은 백엔드에서
            AES-256으로 암호화되어 저장됩니다.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            className="gap-2"
            onClick={() => {
              const session: AuthSession = {
                userId: "00000000-0000-0000-0000-000000000001",
                email: null,
                fullName: "Dev User",
                avatarUrl: null,
                provider: "dev"
              };
              localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
              onAuthenticated(session);
            }}
          >
            <FlaskConical className="h-4 w-4" />
            Dev 모드로 시작
          </Button>
          <Button type="button" variant="ghost" className="gap-2" disabled>
            <KeyRound className="h-4 w-4" />
            클라이언트 ID 적용됨
          </Button>
        </div>
      </Card>
    </main>
  );
}

export function clearAuthSession() {
  localStorage.removeItem(AUTH_STORAGE_KEY);
}
