"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, Plus, Save, Trash2, X } from "lucide-react";

import { PersonaRadar } from "@/components/persona-radar";
import { PerfTrace } from "@/components/perf-trace";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { hasSupabaseEnv, supabase } from "@/lib/supabase";
import {
  ApiKeyMeta,
  AutonomyMode,
  ClaudeConnectionStatus,
  PERSONA_AXES,
  PersonaProfile,
  PersonaStats,
  UserSettings
} from "@/lib/types";
import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer } from "recharts";

type ConnectionSnapshot = {
  claude: ClaudeConnectionStatus;
  school_api: {
    token_saved: boolean;
    reachable?: boolean;
    status?: string;
    reason?: string | null;
    source?: "user_key" | "env_fallback" | "none";
  };
  google_workspace: { token_saved: boolean };
  database?: { connected: boolean; source: "supabase" | "dev_store"; reason?: string | null };
};

type Props = {
  open: boolean;
  userId: string;
  userEmail: string | null;
  settings: UserSettings;
  apiKeys: ApiKeyMeta[];
  connectionStatus: ConnectionSnapshot | null;
  onClose: () => void;
  onSaveSettings: (next: UserSettings) => Promise<void>;
  onSaveApiKey: (keyName: string, plaintextKey: string) => Promise<void>;
  onRefreshConnectionStatus: () => Promise<void>;
};

const EMPTY_STATS: PersonaStats = {
  creativity: 82,
  logic: 76,
  critical_thinking: 79,
  data_dependency: 71,
  empathy: 48,
  drive: 84
};

const CHAT_MODE_LABELS: Record<AutonomyMode, string> = {
  cautious: "신중함",
  balanced: "균형형",
  creative: "창의적",
  autonomous: "완전자율"
};

const GOOGLE_CLIENT_ID =
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ??
  "513803184584-7sb5sp4qv68a534kvd0u3inp0ruf021r.apps.googleusercontent.com";

const GOOGLE_SCOPES = [
  "https://www.googleapis.com/auth/gmail.send",
  "https://www.googleapis.com/auth/calendar.events",
  "https://www.googleapis.com/auth/drive.file"
].join(" ");

type GoogleTokenResponse = {
  access_token?: string;
  expires_in?: number;
  scope?: string;
  error?: string;
};

type GoogleWindow = Window & {
  google?: {
    accounts?: {
      oauth2?: {
        initTokenClient: (config: {
          client_id: string;
          scope: string;
          ux_mode?: "popup" | "redirect";
          callback: (response: GoogleTokenResponse) => void;
          error_callback?: (response: { message?: string; type?: string }) => void;
        }) => {
          requestAccessToken: (params?: { prompt?: string }) => void;
        };
      };
    };
  };
};

function statusChip(healthy: boolean, healthyLabel: string, badLabel: string) {
  return (
    <Badge className={healthy ? "bg-emerald-200/70 text-emerald-900" : "bg-red-200/70 text-red-900"}>
      {healthy ? healthyLabel : badLabel}
    </Badge>
  );
}

function modeApprovalRow(
  label: string,
  checked: boolean,
  onToggle: (value: boolean) => void
) {
  return (
    <label className="flex items-center justify-between rounded-xl border border-white/70 bg-white/45 px-3 py-2 text-sm text-orange-900/85 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onToggle(event.target.checked)}
        className="h-4 w-4 accent-orange-500"
      />
    </label>
  );
}

function normalizeChatModePersonas(
  source: Partial<Record<AutonomyMode, PersonaStats>> | undefined
): Record<AutonomyMode, PersonaStats> {
  return {
    cautious: source?.cautious ?? { ...EMPTY_STATS },
    balanced: source?.balanced ?? { ...EMPTY_STATS },
    creative: source?.creative ?? { ...EMPTY_STATS },
    autonomous: source?.autonomous ?? { ...EMPTY_STATS }
  };
}

function MiniRadarPreview({ value }: { value: PersonaStats }) {
  const data = useMemo(
    () => PERSONA_AXES.map((axis) => ({ axis: axis.label, value: value[axis.key] })),
    [value]
  );
  return (
    <div className="h-[140px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} cx="50%" cy="50%" outerRadius={46} startAngle={90} endAngle={-270}>
          <PolarGrid stroke="#fb923c" strokeOpacity={0.25} />
          <PolarAngleAxis dataKey="axis" tick={false} />
          <Radar
            dataKey="value"
            fill="#fbbf24"
            fillOpacity={0.28}
            stroke="#f97316"
            strokeWidth={1.5}
            isAnimationActive={false}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

export const SettingsModal = memo(function SettingsModal({
  open,
  userId,
  userEmail,
  settings,
  apiKeys,
  connectionStatus,
  onClose,
  onSaveSettings,
  onSaveApiKey,
  onRefreshConnectionStatus
}: Props) {
  const [saving, setSaving] = useState(false);
  const [savingKey, setSavingKey] = useState(false);
  const [schoolApiToken, setSchoolApiToken] = useState("");
  const [googleReady, setGoogleReady] = useState(false);
  const [googleConnecting, setGoogleConnecting] = useState(false);
  const [dbConnecting, setDbConnecting] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [draftSettings, setDraftSettings] = useState<UserSettings>(settings);
  const [showRadar, setShowRadar] = useState(false);
  const [localSavedKeyNames, setLocalSavedKeyNames] = useState<string[]>([]);
  const [activeChatMode, setActiveChatMode] = useState<AutonomyMode>("balanced");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDraftSettings({
      ...settings,
      chat_mode_personas: normalizeChatModePersonas(settings.chat_mode_personas)
    });
    setStatusText(null);
    setLocalSavedKeyNames([]);
    setActiveChatMode("balanced");
    try {
      const raw = localStorage.getItem(`agentgcs_saved_keys_${userId}`);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setLocalSavedKeyNames(parsed.filter((item): item is string => typeof item === "string"));
      }
    } catch {
      setLocalSavedKeyNames([]);
    }
  }, [open, settings, userId]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const requestedTheme = open ? draftSettings.theme : settings.theme;
    const resolved = requestedTheme === "system" ? (media.matches ? "dark" : "light") : requestedTheme;
    document.documentElement.classList.toggle("dark", resolved === "dark");
  }, [draftSettings.theme, open, settings.theme]);

  useEffect(() => {
    if (!open) {
      setShowRadar(false);
      setGoogleReady(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setShowRadar(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const googleWindow = window as GoogleWindow;
    if (googleWindow.google?.accounts?.oauth2) {
      setGoogleReady(true);
      return;
    }

    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => setGoogleReady(true);
    script.onerror = () => setGoogleReady(false);
    document.head.appendChild(script);
    return () => {
      script.remove();
    };
  }, [open]);

  const keyNames = useMemo(() => {
    const names = new Set(apiKeys.map((key) => key.key_name));
    for (const keyName of localSavedKeyNames) {
      names.add(keyName);
    }
    if (connectionStatus?.school_api.token_saved) names.add("school_api_token");
    if (connectionStatus?.google_workspace.token_saved) names.add("google_oauth_access_token");
    return Array.from(names).sort();
  }, [
    apiKeys,
    connectionStatus?.google_workspace.token_saved,
    connectionStatus?.school_api.token_saved,
    localSavedKeyNames
  ]);

  const hasGoogleToken =
    keyNames.includes("google_oauth_access_token") ||
    Boolean(connectionStatus?.google_workspace.token_saved);
  const availableModels = useMemo(() => {
    const raw = connectionStatus?.claude.available_models ?? [];
    if (!Array.isArray(raw) || raw.length === 0) {
      return ["gpt-5.2", "gpt-5", "gpt-4o", "claude-sonnet-4.6", "claude-3-5-sonnet-20241022"];
    }
    return Array.from(new Set(raw)).slice(0, 80);
  }, [connectionStatus?.claude.available_models]);

  const activeTaskPersona = useMemo(() => {
    const target = draftSettings.personas.find(
      (persona) => persona.id === draftSettings.active_persona_id
    );
    return target ?? draftSettings.personas[0] ?? null;
  }, [draftSettings.active_persona_id, draftSettings.personas]);

  const activeChatModeStats = useMemo(
    () => draftSettings.chat_mode_personas[activeChatMode] ?? { ...EMPTY_STATS },
    [activeChatMode, draftSettings.chat_mode_personas]
  );

  const claudeHealthy = connectionStatus?.claude.reachable ?? false;
  const claudeIssueReason =
    connectionStatus?.claude.status && connectionStatus.claude.status !== "ok"
      ? connectionStatus.claude.status
      : null;

  const updateApprovalPolicy = useCallback(
    (patch: Partial<UserSettings["approval_policy"]>) => {
      setDraftSettings((current) => ({
        ...current,
        approval_policy: {
          ...current.approval_policy,
          ...patch
        }
      }));
    },
    []
  );

  const rememberSavedKey = useCallback(
    (keyName: string) => {
      setLocalSavedKeyNames((current) => {
        const next = Array.from(new Set([...current, keyName]));
        try {
          localStorage.setItem(`agentgcs_saved_keys_${userId}`, JSON.stringify(next));
        } catch {
          // ignore local storage errors
        }
        return next;
      });
    },
    [userId]
  );

  const updateTaskPersona = useCallback((personaId: string, patch: Partial<PersonaProfile>) => {
    setDraftSettings((current) => ({
      ...current,
      personas: current.personas.map((persona) =>
        persona.id === personaId ? { ...persona, ...patch } : persona
      )
    }));
  }, []);

  const updateActiveTaskStats = useCallback(
    (nextStats: PersonaStats) => {
      if (!activeTaskPersona) return;
      updateTaskPersona(activeTaskPersona.id, { stats: nextStats });
    },
    [activeTaskPersona, updateTaskPersona]
  );

  const updateChatModeStats = useCallback(
    (mode: AutonomyMode, nextStats: PersonaStats) => {
      setDraftSettings((current) => ({
        ...current,
        chat_mode_personas: {
          ...current.chat_mode_personas,
          [mode]: nextStats
        }
      }));
    },
    []
  );

  const addTaskPersona = useCallback(() => {
    setDraftSettings((current) => {
      const newPersona: PersonaProfile = {
        id: crypto.randomUUID(),
        name: `에이전트 ${current.personas.length + 1}`,
        stats: { ...EMPTY_STATS }
      };
      return {
        ...current,
        personas: [...current.personas, newPersona],
        active_persona_id: newPersona.id
      };
    });
  }, []);

  const removeTaskPersona = useCallback((personaId: string) => {
    setDraftSettings((current) => {
      if (current.personas.length <= 1) return current;
      const filtered = current.personas.filter((persona) => persona.id !== personaId);
      return {
        ...current,
        personas: filtered,
        active_persona_id: filtered[0]?.id ?? null
      };
    });
  }, []);

  const connectGoogleWorkspace = useCallback(async () => {
    const googleWindow = window as GoogleWindow;
    if (!googleWindow.google?.accounts?.oauth2) {
      setStatusText("Google OAuth 스크립트가 준비되지 않았습니다. 잠시 후 다시 시도해주세요.");
      return;
    }

    setGoogleConnecting(true);
    setStatusText(null);

    try {
      await new Promise<void>((resolve, reject) => {
        const tokenClient = googleWindow.google?.accounts?.oauth2?.initTokenClient({
          client_id: GOOGLE_CLIENT_ID,
          scope: GOOGLE_SCOPES,
          ux_mode: "popup",
          callback: (response) => {
            if (response.error || !response.access_token) {
              reject(new Error(response.error ?? "Google OAuth 토큰 발급 실패"));
              return;
            }
            void (async () => {
              await onSaveApiKey("google_oauth_access_token", response.access_token as string);
              rememberSavedKey("google_oauth_access_token");
              const meta = JSON.stringify({
                issued_at: new Date().toISOString(),
                expires_in: response.expires_in ?? null,
                scope: response.scope ?? GOOGLE_SCOPES
              });
              await onSaveApiKey("google_oauth_token_meta", meta);
              rememberSavedKey("google_oauth_token_meta");
              resolve();
            })().catch(reject);
          },
          error_callback: (error) =>
            reject(new Error(error.message || error.type || "Google OAuth 초기화 실패"))
        });

        tokenClient?.requestAccessToken({ prompt: hasGoogleToken ? "" : "consent" });
      });

      await onRefreshConnectionStatus();
      setStatusText("Google Workspace OAuth 토큰이 저장되었습니다.");
    } catch (error) {
      const message = (error as Error).message;
      if (message.includes("redirect_uri_mismatch") || message.includes("origin_mismatch")) {
        const origin = window.location.origin;
        const callback = `${origin}/oauth/google/callback`;
        const localhostHint =
          window.location.hostname === "localhost"
            ? " (로컬 테스트 시 `http://127.0.0.1:3000` 도 함께 등록 권장)"
            : "";
        setStatusText(
          `Google OAuth 400: redirect_uri_mismatch/origin_mismatch. Google Cloud Console의 OAuth 클라이언트(${GOOGLE_CLIENT_ID})에 Authorized JavaScript origins=${origin}, Authorized redirect URIs=${callback} 를 등록 후 다시 시도해주세요.${localhostHint}`
        );
      } else {
        setStatusText(message);
      }
    } finally {
      setGoogleConnecting(false);
    }
  }, [hasGoogleToken, onRefreshConnectionStatus, onSaveApiKey, rememberSavedKey]);

  const connectDatabaseSession = useCallback(async () => {
    if (!hasSupabaseEnv || !supabase) {
      setStatusText("Supabase 환경변수가 없어 DB 로그인을 진행할 수 없습니다.");
      return;
    }
    setDbConnecting(true);
    setStatusText(null);
    try {
      const { data } = await supabase.auth.getSession();
      if (data.session?.user) {
        setStatusText("이미 Supabase DB 세션이 연결되어 있습니다.");
        await onRefreshConnectionStatus();
        return;
      }
      await supabase.auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: window.location.origin }
      });
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setDbConnecting(false);
    }
  }, [onRefreshConnectionStatus]);

  const saveSchoolToken = useCallback(async () => {
    if (!schoolApiToken.trim()) return;
    setSavingKey(true);
    setStatusText(null);
    try {
      await onSaveApiKey("school_api_token", schoolApiToken.trim());
      rememberSavedKey("school_api_token");
      setSchoolApiToken("");
      setStatusText("api.1000.school 토큰이 저장되었습니다.");
      await onRefreshConnectionStatus();
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSavingKey(false);
    }
  }, [onRefreshConnectionStatus, onSaveApiKey, rememberSavedKey, schoolApiToken]);

  const handleSaveSettings = useCallback(async () => {
    setSaving(true);
    setStatusText(null);
    try {
      await onSaveSettings({
        ...draftSettings,
        chat_mode_personas: normalizeChatModePersonas(draftSettings.chat_mode_personas)
      });
      setStatusText("설정이 저장되었습니다.");
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSaving(false);
    }
  }, [draftSettings, onSaveSettings]);

  if (!open) return null;

  return (
    <PerfTrace id="settings-modal" thresholdMs={8}>
      <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/35 p-4">
        <div
          className="max-h-[92vh] w-full max-w-[1280px] overflow-x-hidden overflow-y-auto rounded-3xl border border-white/70 bg-white/95 p-4 shadow-2xl md:p-6 dark:border-slate-700 dark:bg-slate-900/95"
          style={{ contain: "layout paint" }}
        >
          <header className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-gray-800 dark:text-slate-100">계정 및 API 설정</h2>
              <p className="text-sm text-orange-900/70 dark:text-slate-300/80">
                user_id: <span className="font-mono">{userId}</span>
                {userEmail ? ` / ${userEmail}` : " / 이메일 미연결"}
              </p>
            </div>
            <Button type="button" variant="secondary" onClick={onClose} className="gap-1.5">
              <X className="h-4 w-4" />
              닫기
            </Button>
          </header>

          <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
            <div className="space-y-4">
              <Card className="space-y-3">
                <CardTitle>색상 테마</CardTitle>
                <CardDescription>Light / Dark / System (저장 전 미리보기 적용)</CardDescription>
                <ThemeToggle
                  value={draftSettings.theme}
                  onChange={(next) => setDraftSettings((current) => ({ ...current, theme: next }))}
                />
              </Card>

              <Card className="space-y-3">
                <CardTitle>API 연결</CardTitle>
                <CardDescription>
                  `api.1000.school` 토큰은 Claude 연결 토큰으로도 함께 사용됩니다.
                </CardDescription>
                <Input
                  value={schoolApiToken}
                  onChange={(event) => setSchoolApiToken(event.target.value)}
                  placeholder="api.1000.school 토큰 입력"
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="accent"
                    className="gap-1.5"
                    disabled={savingKey || !schoolApiToken.trim()}
                    onClick={() => void saveSchoolToken()}
                  >
                    <Save className="h-4 w-4" />
                    토큰 저장
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void onRefreshConnectionStatus()}
                  >
                    연결 상태 갱신
                  </Button>
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">Claude 연결 진단</p>
                    {statusChip(claudeHealthy, "정상 작동중", "오류")}
                  </div>
                  {!claudeHealthy && (
                    <p className="text-xs text-red-800 dark:text-red-300">
                      사유: {claudeIssueReason ?? "연결 실패"} / 점검: Base URL, 토큰 저장 여부, 게이트웨이 상태
                    </p>
                  )}
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">
                      GCS Pulse(api.1000.school)
                    </p>
                    {statusChip(
                      Boolean(connectionStatus?.school_api.reachable),
                      "정상 작동중",
                      connectionStatus?.school_api.token_saved ? "오류" : "미연결"
                    )}
                  </div>
                  {connectionStatus?.school_api.reason ? (
                    <p className="text-xs text-red-800 dark:text-red-300">
                      사유: {connectionStatus.school_api.reason}
                    </p>
                  ) : (
                    <p className="text-xs text-orange-900/75 dark:text-slate-300/80">
                      상태: {connectionStatus?.school_api.status ?? "unknown"} / source:{" "}
                      {connectionStatus?.school_api.source ?? "none"}
                    </p>
                  )}
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">Google Workspace</p>
                    {statusChip(
                      Boolean(connectionStatus?.google_workspace.token_saved),
                      "정상 작동중",
                      "미연결"
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!googleReady || googleConnecting}
                    onClick={() => void connectGoogleWorkspace()}
                    className="w-full"
                  >
                    {googleConnecting
                      ? "Google OAuth 연결 중..."
                      : hasGoogleToken
                        ? "Google OAuth 재연결"
                        : "Google OAuth 연결"}
                  </Button>
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">Database (Supabase)</p>
                    {statusChip(
                      Boolean(connectionStatus?.database?.connected),
                      "정상 작동중",
                      "미연결"
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={dbConnecting}
                    onClick={() => void connectDatabaseSession()}
                    className="w-full"
                  >
                    {dbConnecting ? "DB 로그인 처리 중..." : "DB 로그인"}
                  </Button>
                  <p className="mt-2 text-xs text-orange-900/70 dark:text-slate-300/80">
                    source: {connectionStatus?.database?.source ?? "unknown"}
                    {connectionStatus?.database?.reason
                      ? ` / reason: ${connectionStatus.database.reason}`
                      : ""}
                  </p>
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 text-xs text-orange-900/80 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                  저장된 키: {keyNames.length > 0 ? keyNames.join(", ") : "없음"}
                </div>
              </Card>

              <Card className="space-y-3">
                <CardTitle>AI 승인 정책</CardTitle>
                <CardDescription>
                  완전자율은 최초 1회 경고 승인 후 자동 진행됩니다. 나머지 모드는 위험 작업(API POST 등)에서만
                  승인 창을 요청합니다.
                </CardDescription>
                <div className="space-y-2">
                  {modeApprovalRow(
                    "신중함 모드 위험 작업 진행시 의무 승인 요청",
                    draftSettings.approval_policy.cautious_requires_approval,
                    (next) => updateApprovalPolicy({ cautious_requires_approval: next })
                  )}
                  {modeApprovalRow(
                    "균형형 모드 위험 작업 진행시 의무 승인 요청",
                    draftSettings.approval_policy.balanced_requires_approval,
                    (next) => updateApprovalPolicy({ balanced_requires_approval: next })
                  )}
                  {modeApprovalRow(
                    "창의적 모드 위험 작업 진행시 의무 승인 요청",
                    draftSettings.approval_policy.creative_requires_approval,
                    (next) => updateApprovalPolicy({ creative_requires_approval: next })
                  )}
                  {modeApprovalRow(
                    "완전자율 모드 최초 경고 및 승인 필요",
                    draftSettings.approval_policy.autonomous_needs_first_warning,
                    (next) => updateApprovalPolicy({ autonomous_needs_first_warning: next })
                  )}
                </div>
                <p className="rounded-xl border border-amber-200/80 bg-amber-50/70 px-3 py-2 text-xs text-amber-900/80 dark:border-amber-700/60 dark:bg-amber-900/20 dark:text-amber-200">
                  완전자율 모드는 최초 승인 이후에는 추가 승인 없이 자동 진행됩니다.
                </p>
              </Card>
            </div>

            <div className="space-y-4">
              <Card className="space-y-3">
                <CardTitle>AI 성향 설정 (대화용 기본 페르소나)</CardTitle>
                <CardDescription>
                  아래 4개 모드는 삭제할 수 없습니다. 대화 시 선택 모드에 따라 해당 성향이 적용됩니다.
                </CardDescription>
                <div className="pb-1">
                  <div className="grid grid-cols-4 gap-2">
                    {(["cautious", "balanced", "creative", "autonomous"] as AutonomyMode[]).map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setActiveChatMode(mode)}
                        className={`rounded-xl border p-2 text-left transition-colors ${
                          activeChatMode === mode
                            ? "border-orange-300 bg-orange-50/70 dark:border-orange-500 dark:bg-orange-500/10"
                            : "border-white/70 bg-white/45 hover:bg-white/70 dark:border-slate-700 dark:bg-slate-800/60 dark:hover:bg-slate-700/70"
                        }`}
                      >
                        <p className="mb-1 text-xs font-semibold text-gray-800 dark:text-slate-100">
                          {CHAT_MODE_LABELS[mode]}
                        </p>
                        <MiniRadarPreview value={draftSettings.chat_mode_personas[mode]} />
                      </button>
                    ))}
                  </div>
                </div>
                {showRadar && (
                  <PersonaRadar
                    value={activeChatModeStats}
                    onChange={(next) => updateChatModeStats(activeChatMode, next)}
                    showHeader
                    title={`${CHAT_MODE_LABELS[activeChatMode]} 모드 세부 조정`}
                    description="해당 모드로 대화할 때 적용되는 기본 AI 성향입니다."
                  />
                )}
              </Card>

              <Card className="space-y-3">
                <CardTitle>과제 해결용 멀티 에이전트 페르소나</CardTitle>
                <CardDescription>
                  과제 자동화에서만 사용하는 에이전트 페르소나입니다. 사용자 성향과는 분리됩니다.
                </CardDescription>
                <div className="flex items-center justify-end">
                  <Button type="button" variant="secondary" className="gap-1.5" onClick={addTaskPersona}>
                    <Plus className="h-4 w-4" />
                    에이전트 추가
                  </Button>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  {draftSettings.personas.map((persona) => (
                    <div
                      key={persona.id}
                      className={`rounded-xl border px-3 py-2 ${
                        draftSettings.active_persona_id === persona.id
                          ? "border-orange-300 bg-orange-50/70 dark:border-orange-500 dark:bg-orange-500/10"
                          : "border-white/70 bg-white/45 dark:border-slate-700 dark:bg-slate-800/60"
                      }`}
                    >
                      <button
                        type="button"
                        className="w-full text-left"
                        onClick={() =>
                          setDraftSettings((current) => ({ ...current, active_persona_id: persona.id }))
                        }
                      >
                        <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">{persona.name}</p>
                        <p className="text-xs text-orange-900/65 dark:text-slate-300/80">{persona.id}</p>
                      </button>
                      <div className="mt-2 flex items-center gap-2">
                        <Input
                          value={persona.name}
                          onChange={(event) => updateTaskPersona(persona.id, { name: event.target.value })}
                          placeholder="에이전트 이름"
                          className="h-9 text-xs"
                        />
                        <Button
                          type="button"
                          size="sm"
                          variant="secondary"
                          onClick={() => removeTaskPersona(persona.id)}
                          disabled={draftSettings.personas.length <= 1}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>

              {activeTaskPersona && showRadar && (
                <PersonaRadar
                  value={activeTaskPersona.stats}
                  onChange={updateActiveTaskStats}
                  showHeader
                  title="선택된 과제 에이전트 성향"
                  description="과제 토론 시작 시 이 성향값이 멀티 에이전트 프롬프트에 주입됩니다."
                />
              )}

              <Card className="space-y-3">
                <CardTitle>사전 지식</CardTitle>
                <CardDescription>
                  대화/요약 시 항상 참고할 지침이나 배경 지식을 저장합니다. (DB 저장)
                </CardDescription>
                <Textarea
                  value={draftSettings.knowledge_base_prompt ?? ""}
                  onChange={(event) =>
                    setDraftSettings((current) => ({
                      ...current,
                      knowledge_base_prompt: event.target.value
                    }))
                  }
                  placeholder="예: 우리 팀의 발표는 기술 정확성보다 비즈니스 임팩트와 실행 가능성을 우선한다."
                  className="min-h-28"
                />
              </Card>

              <Card className="space-y-3">
                <button
                  type="button"
                  className="flex w-full items-center justify-between text-left"
                  onClick={() => setAdvancedOpen((prev) => !prev)}
                >
                  <div>
                    <CardTitle>고급 설정</CardTitle>
                    <CardDescription>접어두기/펼치기 가능</CardDescription>
                  </div>
                  {advancedOpen ? (
                    <ChevronUp className="h-4 w-4 text-orange-900/70 dark:text-slate-300/80" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-orange-900/70 dark:text-slate-300/80" />
                  )}
                </button>
                {advancedOpen && (
                  <div className="space-y-2">
                    <Input
                      value={draftSettings.claude_base_url ?? ""}
                      onChange={(event) =>
                        setDraftSettings((current) => ({
                          ...current,
                          claude_base_url: event.target.value
                        }))
                      }
                      placeholder="Claude Base URL"
                    />
                    <label className="block space-y-1 text-xs text-orange-900/75 dark:text-slate-300/80">
                      <span className="font-semibold">모델 선택 (claude.1000.school 제공 모델)</span>
                      <select
                        value={draftSettings.preferred_model ?? ""}
                        onChange={(event) =>
                          setDraftSettings((current) => ({
                            ...current,
                            preferred_model: event.target.value || null
                          }))
                        }
                        className="h-10 w-full rounded-xl border border-white/80 bg-white/70 px-3 text-sm text-gray-800 outline-none focus:border-orange-300 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-100"
                      >
                        <option value="">자동 선택</option>
                        {availableModels.map((model) => (
                          <option key={model} value={model}>
                            {model}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Input
                      value={draftSettings.preferred_model ?? ""}
                      onChange={(event) =>
                        setDraftSettings((current) => ({
                          ...current,
                          preferred_model: event.target.value
                        }))
                      }
                      placeholder="모델명을 직접 입력할 수도 있습니다."
                    />
                    <p className="text-xs text-orange-900/70 dark:text-slate-300/80">
                      권장: GPT-5 계열 모델(`gpt-5.2`, `gpt-5`) 우선 사용
                    </p>
                  </div>
                )}
              </Card>

              {statusText && (
                <div className="rounded-xl border border-white/70 bg-white/55 px-3 py-2 text-sm text-orange-900/85 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
                  {statusText}
                </div>
              )}

              <div className="flex items-center justify-end gap-2">
                <Button type="button" variant="secondary" onClick={onClose}>
                  취소
                </Button>
                <Button
                  type="button"
                  variant="accent"
                  className="gap-1.5"
                  disabled={saving}
                  onClick={() => void handleSaveSettings()}
                >
                  <Save className="h-4 w-4" />
                  설정 저장
                </Button>
              </div>
            </div>
          </div>

          {connectionStatus && connectionStatus.claude.status === "upstream_502" && (
            <div className="mt-4 flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50/90 p-3 text-sm text-amber-900/90 dark:border-amber-700/60 dark:bg-amber-900/20 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <p>
                `claude.1000.school` 게이트웨이에서 502 응답이 반복되고 있습니다. 토큰 저장은 정상일 수 있으니
                게이트웨이 측 상태를 점검해주세요.
              </p>
            </div>
          )}
        </div>
      </div>
    </PerfTrace>
  );
});
