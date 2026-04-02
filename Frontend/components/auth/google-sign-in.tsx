"use client";

import { useEffect, useMemo, useState } from "react";
import { LogOut, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { hasSupabaseEnv, supabase } from "@/lib/supabase";

type Props = {
  onUserChange: (userId: string, email?: string | null) => void;
};

const DEMO_USER_ID = "00000000-0000-0000-0000-000000000001";

export function GoogleSignInCard({ onUserChange }: Props) {
  const [email, setEmail] = useState<string | null>(null);
  const [userId, setUserId] = useState<string>(DEMO_USER_ID);

  useEffect(() => {
    if (!supabase) {
      onUserChange(DEMO_USER_ID, "demo@local");
      return;
    }

    let mounted = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return;
      const uid = data.session?.user.id ?? DEMO_USER_ID;
      const nextEmail = data.session?.user.email ?? "demo@local";
      setUserId(uid);
      setEmail(nextEmail);
      onUserChange(uid, nextEmail);
    });

    const {
      data: { subscription }
    } = supabase.auth.onAuthStateChange((_event, session) => {
      const uid = session?.user.id ?? DEMO_USER_ID;
      const nextEmail = session?.user.email ?? "demo@local";
      setUserId(uid);
      setEmail(nextEmail);
      onUserChange(uid, nextEmail);
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, [onUserChange]);

  const authModeText = useMemo(
    () =>
      hasSupabaseEnv ? "Supabase Google OAuth 활성" : "Demo 모드 (Supabase env 없음)",
    []
  );

  async function signInWithGoogle() {
    if (!supabase) return;
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin }
    });
  }

  async function signOut() {
    if (!supabase) return;
    await supabase.auth.signOut();
  }

  return (
    <Card className="space-y-3">
      <CardTitle>사용자 인증</CardTitle>
      <CardDescription>
        {authModeText}
        <br />
        user_id: <span className="font-mono text-xs">{userId}</span>
      </CardDescription>
      <div className="flex items-center gap-2">
        {hasSupabaseEnv ? (
          <>
            <Button
              variant="accent"
              onClick={signInWithGoogle}
              className="gap-2"
              disabled={Boolean(email)}
            >
              <UserRound className="h-4 w-4" />
              Google 로그인
            </Button>
            <Button
              variant="secondary"
              onClick={signOut}
              className="gap-2"
              disabled={!email}
            >
              <LogOut className="h-4 w-4" />
              로그아웃
            </Button>
          </>
        ) : (
          <Button variant="secondary" disabled>
            Demo 모드로 진행
          </Button>
        )}
      </div>
      {email && (
        <p className="text-xs text-surface-800/75">
          로그인 이메일: <span className="font-semibold">{email}</span>
        </p>
      )}
    </Card>
  );
}

