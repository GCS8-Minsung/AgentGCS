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
  normalizePersonaStats,
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
  google_workspace: {
    token_saved: boolean;
    reachable?: boolean;
    status?: string;
    reason?: string | null;
    oauth_configured?: boolean;
    token_expired?: boolean;
    refresh_available?: boolean;
    connected_account?: {
      email?: string | null;
      name?: string | null;
      sub?: string | null;
      verified_email?: boolean;
    };
    drive_mapping?: {
      input_folder_id?: string | null;
      output_folder_id?: string | null;
      configured?: boolean;
      input_configured?: boolean;
      output_configured?: boolean;
    };
    service_account?: {
      project_id?: string | null;
      client_email?: string | null;
      configured?: boolean;
    };
    services?: {
      drive?: { status?: string; http_status?: number; reason?: string };
      gmail?: { status?: string; http_status?: number; reason?: string };
      calendar?: { status?: string; http_status?: number; reason?: string };
    };
  };
  openai_fallback?: {
    token_saved: boolean;
    reachable?: boolean;
    status?: string;
    model?: string;
  };
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
  cautiousness: 48,
  drive: 84
};

const CHAT_MODE_LABELS: Record<AutonomyMode, string> = {
  cautious: "신중함",
  balanced: "균형형",
  creative: "창의적",
  autonomous: "완전자율"
};
const RESERVED_PERSONA_ID = "default-balanced";
const MAX_TASK_PERSONAS = 6;
const MIN_DISCUSSION_ROUNDS = 2;
const MAX_DISCUSSION_ROUNDS = 5;

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
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
    cautious: normalizePersonaStats(source?.cautious ?? EMPTY_STATS),
    balanced: normalizePersonaStats(source?.balanced ?? EMPTY_STATS),
    creative: normalizePersonaStats(source?.creative ?? EMPTY_STATS),
    autonomous: normalizePersonaStats(source?.autonomous ?? EMPTY_STATS)
  };
}

function normalizeDiscussionRounds(value: unknown): number {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return 3;
  const rounded = Math.round(numeric);
  return Math.max(MIN_DISCUSSION_ROUNDS, Math.min(MAX_DISCUSSION_ROUNDS, rounded));
}

function normalizeTaskPersonas(source: PersonaProfile[] | undefined): PersonaProfile[] {
  const rows = Array.isArray(source) ? source : [];
  const normalized: PersonaProfile[] = [];
  const seen = new Set<string>();

  const defaultPersona = rows.find((row) => row?.id === RESERVED_PERSONA_ID);
  const fallbackDefault: PersonaProfile = {
    id: RESERVED_PERSONA_ID,
    name: "기본 균형형",
    stats: { ...EMPTY_STATS }
  };
  const ensuredDefault = defaultPersona
    ? {
        ...defaultPersona,
        id: RESERVED_PERSONA_ID,
        name: defaultPersona.name?.trim() || "기본 균형형",
        stats: normalizePersonaStats(defaultPersona.stats)
      }
    : fallbackDefault;
  normalized.push(ensuredDefault);
  seen.add(RESERVED_PERSONA_ID);

  for (const row of rows) {
    if (!row?.id || seen.has(row.id)) continue;
    if (normalized.length >= MAX_TASK_PERSONAS) break;
    normalized.push({
      ...row,
      id: row.id,
      name: row.name?.trim() || row.id,
      stats: normalizePersonaStats(row.stats)
    });
    seen.add(row.id);
  }
  return normalized;
}

function extractBearerToken(raw: string): string {
  const text = raw.trim();
  if (!text) return "";
  const bearerMatch = text.match(/Bearer\s+([A-Za-z0-9._\-]+)/i);
  if (bearerMatch?.[1]) return bearerMatch[1].trim();
  const keyMatch = text.match(/(sk-[A-Za-z0-9._\-]+)/);
  if (keyMatch?.[1]) return keyMatch[1].trim();
  return text;
}

function MiniRadarPreview({ value }: { value: PersonaStats }) {
  const normalized = useMemo(() => normalizePersonaStats(value), [value]);
  const data = useMemo(
    () => PERSONA_AXES.map((axis) => ({ axis: axis.label, value: normalized[axis.key] })),
    [normalized]
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
  const [openAiApiToken, setOpenAiApiToken] = useState("");
  const [googleClientIdInput, setGoogleClientIdInput] = useState("");
  const [googleClientSecretInput, setGoogleClientSecretInput] = useState("");
  const [googleDriveInputFolderId, setGoogleDriveInputFolderId] = useState("");
  const [googleDriveOutputFolderId, setGoogleDriveOutputFolderId] = useState("");
  const [serviceAccountProjectId, setServiceAccountProjectId] = useState("");
  const [serviceAccountClientEmail, setServiceAccountClientEmail] = useState("");
  const [googleReady, setGoogleReady] = useState(false);
  const [googleConnecting, setGoogleConnecting] = useState(false);
  const [dbConnecting, setDbConnecting] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [draftSettings, setDraftSettings] = useState<UserSettings>(settings);
  const [showRadar, setShowRadar] = useState(false);
  const [localSavedKeyNames, setLocalSavedKeyNames] = useState<string[]>([]);
  const [activeChatMode, setActiveChatMode] = useState<AutonomyMode>("balanced");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const showDevDetails = Boolean(draftSettings.dev_mode);

  useEffect(() => {
    if (!open) return;
    const normalizedPersonas = normalizeTaskPersonas(settings.personas);
    setDraftSettings({
      ...settings,
      personas: normalizedPersonas,
      active_persona_id:
        normalizedPersonas.find((persona) => persona.id === settings.active_persona_id)?.id ??
        normalizedPersonas[0]?.id ??
        RESERVED_PERSONA_ID,
      discussion_rounds: normalizeDiscussionRounds(settings.discussion_rounds),
      notebooklm_profile: settings.notebooklm_profile?.trim() || null,
      notebooklm_allow_oauth_mismatch: settings.notebooklm_allow_oauth_mismatch ?? true,
      notebooklm_auto_switch_on_slide_failure:
        settings.notebooklm_auto_switch_on_slide_failure ?? true,
      chat_mode_personas: normalizeChatModePersonas(settings.chat_mode_personas)
    });
    setStatusText(null);
    setLocalSavedKeyNames([]);
    setSchoolApiToken("");
    setOpenAiApiToken("");
    setGoogleClientIdInput("");
    setGoogleClientSecretInput("");
    setGoogleDriveInputFolderId(
      String(connectionStatus?.google_workspace.drive_mapping?.input_folder_id ?? "").trim()
    );
    setGoogleDriveOutputFolderId(
      String(connectionStatus?.google_workspace.drive_mapping?.output_folder_id ?? "").trim()
    );
    setServiceAccountProjectId(
      String(connectionStatus?.google_workspace.service_account?.project_id ?? "").trim()
    );
    setServiceAccountClientEmail(
      String(connectionStatus?.google_workspace.service_account?.client_email ?? "").trim()
    );
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
  }, [connectionStatus, open, settings, userId]);

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
    return () => script.remove();
  }, [open]);

  useEffect(() => {
    const markSaved = (keyName: string) => {
      setLocalSavedKeyNames((current) => {
        const next = Array.from(new Set([...current, keyName]));
        try {
          localStorage.setItem(`agentgcs_saved_keys_${userId}`, JSON.stringify(next));
        } catch {
          // ignore local storage errors
        }
        return next;
      });
    };
    const backendOrigin = (() => {
      try {
        return new URL(BACKEND_URL).origin;
      } catch {
        return null;
      }
    })();
    const handler = (event: MessageEvent) => {
      if (backendOrigin && event.origin !== backendOrigin) return;
      const payload = event.data as
        | { type?: string; status?: string; message?: string }
        | undefined;
      if (!payload || payload.type !== "agentgcs_google_oauth_result") return;
      setGoogleConnecting(false);
      if (payload.status === "success") {
        markSaved("google_oauth_access_token");
        markSaved("google_oauth_token_meta");
        setStatusText(payload.message ?? "Google Workspace OAuth 연결이 완료되었습니다.");
      } else {
        setStatusText(payload.message ?? "Google Workspace OAuth 연결에 실패했습니다.");
      }
      void onRefreshConnectionStatus();
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [onRefreshConnectionStatus, userId]);

  const keyNames = useMemo(() => {
    const names = new Set(apiKeys.map((key) => key.key_name));
    for (const keyName of localSavedKeyNames) {
      names.add(keyName);
    }
    if (connectionStatus?.school_api.token_saved) names.add("school_api_token");
    if (connectionStatus?.google_workspace.token_saved) names.add("google_oauth_access_token");
    if (connectionStatus?.openai_fallback?.token_saved) names.add("openai_api_key");
    return Array.from(names).sort();
  }, [
    apiKeys,
    connectionStatus?.openai_fallback?.token_saved,
    connectionStatus?.google_workspace.token_saved,
    connectionStatus?.school_api.token_saved,
    localSavedKeyNames
  ]);

  const hasGoogleToken =
    keyNames.includes("google_oauth_access_token") ||
    keyNames.includes("google_oauth_refresh_token") ||
    Boolean(connectionStatus?.google_workspace.token_saved);
  const hasOpenAiFallbackKey =
    keyNames.includes("openai_api_key") ||
    Boolean(connectionStatus?.openai_fallback?.token_saved);
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
        persona.id === personaId
          ? {
              ...persona,
              ...patch,
              stats: patch.stats
                ? normalizePersonaStats(patch.stats)
                : normalizePersonaStats(persona.stats)
            }
          : persona
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
          [mode]: normalizePersonaStats(nextStats)
        }
      }));
    },
    []
  );

  const addTaskPersona = useCallback(() => {
    setDraftSettings((current) => {
      if (current.personas.length >= MAX_TASK_PERSONAS) {
        return current;
      }
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
      if (personaId === RESERVED_PERSONA_ID) return current;
      if (current.personas.length <= 1) return current;
      const filtered = current.personas.filter((persona) => persona.id !== personaId);
      return {
        ...current,
        personas: filtered,
        active_persona_id:
          current.active_persona_id === personaId
            ? (filtered[0]?.id ?? RESERVED_PERSONA_ID)
            : current.active_persona_id
      };
    });
  }, []);

  const connectGoogleWorkspace = useCallback(async () => {
    if (!userId) {
      setStatusText("사용자 식별 정보가 없어 Google OAuth를 시작할 수 없습니다.");
      return;
    }
    setGoogleConnecting(true);
    setStatusText(null);

    if (!connectionStatus?.google_workspace.oauth_configured) {
      const googleWindow = window as GoogleWindow;
      if (!googleWindow.google?.accounts?.oauth2) {
        setGoogleConnecting(false);
        setStatusText(
          "백엔드 OAuth 미설정 + Google OAuth 스크립트 미준비 상태입니다. 잠시 후 다시 시도하거나 백엔드 OAuth를 설정해주세요."
        );
        return;
      }
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
                  scope: response.scope ?? GOOGLE_SCOPES,
                  source: "frontend_token_fallback"
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
        setStatusText(
          "Google OAuth 연결 완료(프론트 토큰 모드). 장기 안정화를 위해 백엔드 OAuth 설정도 권장됩니다."
        );
        await onRefreshConnectionStatus();
      } catch (error) {
        setStatusText((error as Error).message);
      } finally {
        setGoogleConnecting(false);
      }
      return;
    }

    const returnTo = window.location.href;
    const startUrl = `${BACKEND_URL}/api/google/oauth/start?user_id=${encodeURIComponent(
      userId
    )}&return_to=${encodeURIComponent(returnTo)}`;
    const popup = window.open(
      startUrl,
      "agentgcs_google_oauth",
      "width=560,height=760,menubar=no,toolbar=no,status=no"
    );
    if (!popup) {
      setGoogleConnecting(false);
      setStatusText("브라우저 팝업이 차단되어 OAuth를 시작할 수 없습니다.");
      return;
    }
    const poll = window.setInterval(() => {
      if (popup.closed) {
        window.clearInterval(poll);
        setGoogleConnecting(false);
        void onRefreshConnectionStatus();
      }
    }, 600);
    window.setTimeout(() => window.clearInterval(poll), 120000);
  }, [
    connectionStatus?.google_workspace.oauth_configured,
    hasGoogleToken,
    onRefreshConnectionStatus,
    onSaveApiKey,
    rememberSavedKey,
    userId
  ]);

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

  const saveOpenAiToken = useCallback(async () => {
    const normalized = extractBearerToken(openAiApiToken);
    if (!normalized) return;
    setSavingKey(true);
    setStatusText(null);
    try {
      await onSaveApiKey("openai_api_key", normalized);
      rememberSavedKey("openai_api_key");
      setOpenAiApiToken("");
      setStatusText("OpenAI 예비용 API 키가 저장되었습니다. (Claude 429 시 자동 폴백)");
      await onRefreshConnectionStatus();
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSavingKey(false);
    }
  }, [onRefreshConnectionStatus, onSaveApiKey, openAiApiToken, rememberSavedKey]);

  const saveGoogleOAuthClient = useCallback(async () => {
    const normalizedClientId = googleClientIdInput.trim();
    const normalizedSecret = googleClientSecretInput.trim();
    if (!normalizedClientId && !normalizedSecret) return;
    setSavingKey(true);
    setStatusText(null);
    try {
      if (normalizedClientId) {
        await onSaveApiKey("google_client_id", normalizedClientId);
        rememberSavedKey("google_client_id");
      }
      if (normalizedSecret) {
        await onSaveApiKey("google_client_secret", normalizedSecret);
        rememberSavedKey("google_client_secret");
      }
      setGoogleClientIdInput("");
      setGoogleClientSecretInput("");
      setStatusText("Google OAuth 클라이언트 정보가 저장되었습니다.");
      await onRefreshConnectionStatus();
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSavingKey(false);
    }
  }, [
    googleClientIdInput,
    googleClientSecretInput,
    onRefreshConnectionStatus,
    onSaveApiKey,
    rememberSavedKey
  ]);

  const saveGoogleDriveMapping = useCallback(async () => {
    const inputId = googleDriveInputFolderId.trim();
    const outputId = googleDriveOutputFolderId.trim();
    const projectId = serviceAccountProjectId.trim();
    const clientEmail = serviceAccountClientEmail.trim();
    if (!inputId && !outputId && !projectId && !clientEmail) return;
    setSavingKey(true);
    setStatusText(null);
    try {
      if (inputId) {
        await onSaveApiKey("google_drive_input_root_folder_id", inputId);
        await onSaveApiKey("google_drive_input_folder_id", inputId);
        rememberSavedKey("google_drive_input_root_folder_id");
        rememberSavedKey("google_drive_input_folder_id");
      }
      if (outputId) {
        await onSaveApiKey("google_drive_output_root_folder_id", outputId);
        await onSaveApiKey("google_drive_output_folder_id", outputId);
        rememberSavedKey("google_drive_output_root_folder_id");
        rememberSavedKey("google_drive_output_folder_id");
      }
      if (projectId) {
        await onSaveApiKey("google_service_account_project_id", projectId);
        rememberSavedKey("google_service_account_project_id");
      }
      if (clientEmail) {
        await onSaveApiKey("google_service_account_client_email", clientEmail);
        rememberSavedKey("google_service_account_client_email");
      }
      setStatusText("Google Drive 기본 경로/계정 매핑이 저장되었습니다.");
      await onRefreshConnectionStatus();
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSavingKey(false);
    }
  }, [
    googleDriveInputFolderId,
    googleDriveOutputFolderId,
    serviceAccountProjectId,
    serviceAccountClientEmail,
    onSaveApiKey,
    rememberSavedKey,
    onRefreshConnectionStatus
  ]);

  const handleSaveSettings = useCallback(async () => {
    setSaving(true);
    setStatusText(null);
    try {
      const normalizedPersonas = normalizeTaskPersonas(draftSettings.personas);
      const activePersonaId =
        normalizedPersonas.find((persona) => persona.id === draftSettings.active_persona_id)?.id ??
        normalizedPersonas[0]?.id ??
        RESERVED_PERSONA_ID;
      await onSaveSettings({
        ...draftSettings,
        notebooklm_profile: draftSettings.notebooklm_profile?.trim() || null,
        notebooklm_allow_oauth_mismatch: Boolean(draftSettings.notebooklm_allow_oauth_mismatch),
        notebooklm_auto_switch_on_slide_failure: Boolean(
          draftSettings.notebooklm_auto_switch_on_slide_failure
        ),
        personas: normalizedPersonas,
        active_persona_id: activePersonaId,
        discussion_rounds: normalizeDiscussionRounds(draftSettings.discussion_rounds),
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
                {showDevDetails ? (
                  <>
                    user_id: <span className="font-mono">{userId}</span>
                    {userEmail ? ` / ${userEmail}` : " / 이메일 미연결"}
                  </>
                ) : (
                  <>{userEmail ? userEmail : "이메일 미연결"}</>
                )}
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
                  일반 대화는 ChatGPT, 고급 작업/토론은 Claude(실패 시 ChatGPT 폴백)로 자동 라우팅됩니다.
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

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 text-xs text-orange-900/80 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                  <p className="mb-2 font-semibold text-gray-800 dark:text-slate-100">
                    Google OAuth 정식 모드 설정
                  </p>
                  <Input
                    value={googleClientIdInput}
                    onChange={(event) => setGoogleClientIdInput(event.target.value)}
                    placeholder="Google OAuth Client ID (미입력 시 기본값 사용)"
                  />
                  <Input
                    value={googleClientSecretInput}
                    onChange={(event) => setGoogleClientSecretInput(event.target.value)}
                    placeholder="Google OAuth Client Secret"
                    className="mt-2"
                    type="password"
                  />
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={savingKey || (!googleClientIdInput.trim() && !googleClientSecretInput.trim())}
                      onClick={() => void saveGoogleOAuthClient()}
                    >
                      OAuth 클라이언트 저장
                    </Button>
                    {statusChip(
                      Boolean(connectionStatus?.google_workspace.oauth_configured),
                      "정식 모드 준비됨",
                      "정식 모드 미설정"
                    )}
                  </div>
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
                  {showDevDetails ? (
                    connectionStatus?.school_api.reason ? (
                      <p className="text-xs text-red-800 dark:text-red-300">
                        사유: {connectionStatus.school_api.reason}
                      </p>
                    ) : (
                      <p className="text-xs text-orange-900/75 dark:text-slate-300/80">
                        상태: {connectionStatus?.school_api.status ?? "unknown"} / source:{" "}
                        {connectionStatus?.school_api.source ?? "none"}
                      </p>
                    )
                  ) : null}
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">Google Workspace</p>
                    {statusChip(
                      Boolean(connectionStatus?.google_workspace.reachable),
                      "정상 작동중",
                      connectionStatus?.google_workspace.token_saved ? "부분 오류" : "미연결"
                    )}
                  </div>
                  <p className="mb-2 text-xs text-orange-900/75 dark:text-slate-300/80">
                    OAuth client: {connectionStatus?.google_workspace.oauth_configured ? "configured" : "missing"} /{" "}
                    상태: {connectionStatus?.google_workspace.status ?? "unknown"}
                    {connectionStatus?.google_workspace.token_expired ? " / access token 만료" : ""}
                    {connectionStatus?.google_workspace.refresh_available ? " / refresh token 보유" : ""}
                    {showDevDetails && connectionStatus?.google_workspace.reason
                      ? ` / reason: ${connectionStatus.google_workspace.reason}`
                      : ""}
                  </p>
                  {connectionStatus?.google_workspace.connected_account?.email && (
                    <p className="mb-2 text-xs text-orange-900/75 dark:text-slate-300/80">
                      연결 계정: {connectionStatus.google_workspace.connected_account.email}
                    </p>
                  )}
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={googleConnecting || (!googleReady && !connectionStatus?.google_workspace.oauth_configured)}
                    onClick={() => void connectGoogleWorkspace()}
                    className="w-full"
                  >
                    {googleConnecting
                      ? "Google OAuth 연결 중..."
                      : hasGoogleToken
                        ? "Google OAuth 재연결"
                      : "Google OAuth 연결"}
                  </Button>
                  <div className="mt-3 space-y-2 rounded-xl border border-white/70 bg-white/55 p-3 dark:border-slate-700 dark:bg-slate-900/40">
                    <p className="text-xs font-semibold text-gray-800 dark:text-slate-100">
                      NotebookLM 계정 전환
                    </p>
                    <Input
                      value={draftSettings.notebooklm_profile ?? ""}
                      onChange={(event) =>
                        setDraftSettings((current) => ({
                          ...current,
                          notebooklm_profile: event.target.value || null
                        }))
                      }
                      placeholder="NotebookLM 프로필명 또는 이메일 (예: personal / user@domain.com)"
                    />
                    <label className="flex items-center justify-between rounded-xl border border-white/70 bg-white/50 px-3 py-2 text-xs text-orange-900/85 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
                      <span>OAuth와 NotebookLM 계정 다름 허용</span>
                      <input
                        type="checkbox"
                        checked={Boolean(draftSettings.notebooklm_allow_oauth_mismatch)}
                        onChange={(event) =>
                          setDraftSettings((current) => ({
                            ...current,
                            notebooklm_allow_oauth_mismatch: event.target.checked
                          }))
                        }
                        className="h-4 w-4 accent-orange-500"
                      />
                    </label>
                    <label className="flex items-center justify-between rounded-xl border border-white/70 bg-white/50 px-3 py-2 text-xs text-orange-900/85 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
                      <span>슬라이드 실패 시 다른 NotebookLM 프로필 자동 전환</span>
                      <input
                        type="checkbox"
                        checked={Boolean(draftSettings.notebooklm_auto_switch_on_slide_failure)}
                        onChange={(event) =>
                          setDraftSettings((current) => ({
                            ...current,
                            notebooklm_auto_switch_on_slide_failure: event.target.checked
                          }))
                        }
                        className="h-4 w-4 accent-orange-500"
                      />
                    </label>
                    <p className="text-[11px] text-orange-900/75 dark:text-slate-300/80">
                      비워두면 OAuth 연결 계정을 우선 사용합니다. 슬라이드 한도/권한 오류 시 저장된 프로필을 순회해
                      가능한 계정으로 자동 전환합니다.
                    </p>
                  </div>
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800 dark:text-slate-100">
                      Google Drive 기본 경로 매핑
                    </p>
                    {statusChip(
                      Boolean(connectionStatus?.google_workspace.drive_mapping?.configured),
                      "정상 작동중",
                      "부분 설정"
                    )}
                  </div>
                  <div className="space-y-2">
                    <Input
                      value={googleDriveInputFolderId}
                      onChange={(event) => setGoogleDriveInputFolderId(event.target.value)}
                      placeholder="Input Folder ID (예: 1eflSS...)"
                    />
                    <Input
                      value={googleDriveOutputFolderId}
                      onChange={(event) => setGoogleDriveOutputFolderId(event.target.value)}
                      placeholder="Output Folder ID (예: 19u-3v...)"
                    />
                    <Input
                      value={serviceAccountProjectId}
                      onChange={(event) => setServiceAccountProjectId(event.target.value)}
                      placeholder="Service Account Project ID (옵션)"
                    />
                    <Input
                      value={serviceAccountClientEmail}
                      onChange={(event) => setServiceAccountClientEmail(event.target.value)}
                      placeholder="Service Account Client Email (옵션)"
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={
                        savingKey ||
                        (!googleDriveInputFolderId.trim() &&
                          !googleDriveOutputFolderId.trim() &&
                          !serviceAccountProjectId.trim() &&
                          !serviceAccountClientEmail.trim())
                      }
                      onClick={() => void saveGoogleDriveMapping()}
                    >
                      Drive 매핑 저장
                    </Button>
                    {statusChip(
                      Boolean(connectionStatus?.google_workspace.drive_mapping?.input_configured),
                      "Input 설정됨",
                      "Input 미설정"
                    )}
                    {statusChip(
                      Boolean(connectionStatus?.google_workspace.drive_mapping?.output_configured),
                      "Output 설정됨",
                      "Output 미설정"
                    )}
                  </div>
                  <p className="mt-2 text-xs text-orange-900/75 dark:text-slate-300/80">
                    과제 자동화는 여기 저장된 Drive Input/Output 폴더 ID를 우선 사용합니다.
                  </p>
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
                    {showDevDetails ? (
                      <>
                        source: {connectionStatus?.database?.source ?? "unknown"}
                        {connectionStatus?.database?.reason
                          ? ` / reason: ${connectionStatus.database.reason}`
                          : ""}
                      </>
                    ) : (
                      <>상세 진단은 Dev 모드에서 확인할 수 있습니다.</>
                    )}
                  </p>
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 text-xs text-orange-900/80 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                  <p className="mb-2 font-semibold text-gray-800 dark:text-slate-100">
                    OpenAI 예비용 API (Claude 429 대비)
                  </p>
                  <Input
                    value={openAiApiToken}
                    onChange={(event) => setOpenAiApiToken(event.target.value)}
                    placeholder="OpenAI API Key 입력 (sk-...)"
                  />
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={savingKey || !openAiApiToken.trim()}
                      onClick={() => void saveOpenAiToken()}
                    >
                      예비 키 저장
                    </Button>
                    {statusChip(
                      hasOpenAiFallbackKey,
                      "폴백 준비됨",
                      "미설정"
                    )}
                  </div>
                </div>

                <div className="rounded-xl border border-white/70 bg-white/50 p-3 text-xs text-orange-900/80 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                  상태 표시: 연결 뱃지가 `정상 작동중`이면 저장/연결이 정상입니다.
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
                  과제 자동화에서만 사용하는 에이전트 페르소나입니다. 최소 3개, 최대 6개까지 사용됩니다.
                  </CardDescription>
                <div className="flex items-center justify-between">
                  <p className="text-xs text-orange-900/75 dark:text-slate-300/80">
                    현재 {draftSettings.personas.length}/{MAX_TASK_PERSONAS}개
                  </p>
                  <Button
                    type="button"
                    variant="secondary"
                    className="gap-1.5"
                    onClick={addTaskPersona}
                    disabled={draftSettings.personas.length >= MAX_TASK_PERSONAS}
                  >
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
                        <p className="text-xs text-orange-900/65 dark:text-slate-300/80">
                          {persona.id}
                          {persona.id === RESERVED_PERSONA_ID ? " (system)" : ""}
                        </p>
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
                          disabled={
                            draftSettings.personas.length <= 1 || persona.id === RESERVED_PERSONA_ID
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="rounded-xl border border-white/70 bg-white/50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                  <p className="mb-2 text-sm font-semibold text-gray-800 dark:text-slate-100">
                    토론 라운드 수 (2~5)
                  </p>
                  <select
                    value={String(normalizeDiscussionRounds(draftSettings.discussion_rounds))}
                    onChange={(event) =>
                      setDraftSettings((current) => ({
                        ...current,
                        discussion_rounds: normalizeDiscussionRounds(event.target.value)
                      }))
                    }
                    className="h-10 w-full rounded-xl border border-white/80 bg-white/70 px-3 text-sm text-gray-800 outline-none focus:border-orange-300 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-100"
                  >
                    <option value="2">2 라운드</option>
                    <option value="3">3 라운드</option>
                    <option value="4">4 라운드</option>
                    <option value="5">5 라운드</option>
                  </select>
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
                  <div className="space-y-3">
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
                    <div className="rounded-xl border border-white/70 bg-white/45 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                      <p className="mb-2 text-xs font-semibold text-orange-900/80 dark:text-slate-200">
                        Claude 메인 모델
                      </p>
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
                      <Input
                        value={draftSettings.preferred_model ?? ""}
                        onChange={(event) =>
                          setDraftSettings((current) => ({
                            ...current,
                            preferred_model: event.target.value
                          }))
                        }
                        placeholder="Claude 모델명을 직접 입력"
                        className="mt-2"
                      />
                    </div>

                    <div className="rounded-xl border border-white/70 bg-white/45 p-3 dark:border-slate-700 dark:bg-slate-800/60">
                      <p className="mb-2 text-xs font-semibold text-orange-900/80 dark:text-slate-200">
                        ChatGPT 메인 모델
                      </p>
                      <select
                        value={draftSettings.openai_preferred_model ?? ""}
                        onChange={(event) =>
                          setDraftSettings((current) => ({
                            ...current,
                            openai_preferred_model: event.target.value || null
                          }))
                        }
                        className="h-10 w-full rounded-xl border border-white/80 bg-white/70 px-3 text-sm text-gray-800 outline-none focus:border-orange-300 dark:border-slate-700 dark:bg-slate-800/80 dark:text-slate-100"
                      >
                        <option value="">자동 선택</option>
                        {["gpt-5-mini", "gpt-5.2", "gpt-5", "gpt-4o", "gpt-4.1"].map((model) => (
                          <option key={model} value={model}>
                            {model}
                          </option>
                        ))}
                      </select>
                      <Input
                        value={draftSettings.openai_preferred_model ?? ""}
                        onChange={(event) =>
                          setDraftSettings((current) => ({
                            ...current,
                            openai_preferred_model: event.target.value
                          }))
                        }
                        placeholder="GPT 모델명을 직접 입력"
                        className="mt-2"
                      />
                    </div>
                    <p className="text-xs text-orange-900/70 dark:text-slate-300/80">
                      작업 난이도에 따라 백엔드가 자동 라우팅하며, 여기서는 Claude/ChatGPT 기본 모델만 지정합니다.
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
