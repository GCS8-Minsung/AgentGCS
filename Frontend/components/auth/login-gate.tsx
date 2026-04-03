"use client";

import { useEffect, useState } from "react";
import { KeyRound, LogIn } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { hasSupabaseEnv, supabase } from "@/lib/supabase";

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

function mapSupabaseUserToSession(user: {
  id: string;
  email?: string | null;
  user_metadata?: Record<string, unknown>;
}): AuthSession {
  return {
    userId: user.id,
    email: user.email ?? null,
    fullName: typeof user.user_metadata?.full_name === "string" ? user.user_metadata.full_name : null,
    avatarUrl: typeof user.user_metadata?.avatar_url === "string" ? user.user_metadata.avatar_url : null,
    provider: "google"
  };
}

export function LoginGate({ onAuthenticated }: Props) {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;

    async function initialize() {
      if (hasSupabaseEnv && supabase) {
        const { data } = await supabase.auth.getSession();
        if (!mounted) return;
        if (data.session?.user) {
          const mapped = mapSupabaseUserToSession(data.session.user);
          localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(mapped));
          onAuthenticated(mapped);
          return;
        }
      }

      if (mounted) {
        setLoading(false);
      }
    }

    void initialize();

    if (!hasSupabaseEnv || !supabase) {
      return () => {
        mounted = false;
      };
    }

    const {
      data: { subscription }
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
      if (session?.user) {
        const mapped = mapSupabaseUserToSession(session.user);
        localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(mapped));
        onAuthenticated(mapped);
      } else {
        localStorage.removeItem(AUTH_STORAGE_KEY);
        setLoading(false);
      }
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, [onAuthenticated]);

  async function signInWithGoogle() {
    if (!supabase) return;
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: window.location.origin
      }
    });
  }

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
          {hasSupabaseEnv && supabase ? (
            <Button type="button" variant="accent" className="gap-2" onClick={() => void signInWithGoogle()}>
              <LogIn className="h-4 w-4" />
              Google로 로그인
            </Button>
          ) : (
            <p className="text-xs text-orange-900/75">
              Supabase 환경변수가 없어 Google OAuth 로그인을 시작할 수 없습니다.
            </p>
          )}
        </div>

        <div className="rounded-2xl border border-amber-200/70 bg-amber-50/70 p-4 text-sm text-amber-900/85">
          <p className="font-semibold">최초 접속 안내</p>
          <p className="mt-1">
            Google 로그인 후 `api.1000.school` 토큰 입력을 요청합니다. 입력된 토큰은 백엔드에서
            AES-256으로 암호화되어 저장됩니다.
          </p>
        </div>

        <Button type="button" variant="ghost" className="gap-2" disabled>
          <KeyRound className="h-4 w-4" />
          {hasSupabaseEnv ? "Supabase OAuth 활성" : "Supabase 키 미설정"}
        </Button>
      </Card>
    </main>
  );
}

export function clearAuthSession() {
  localStorage.removeItem(AUTH_STORAGE_KEY);
  if (supabase) {
    void supabase.auth.signOut();
  }
}
