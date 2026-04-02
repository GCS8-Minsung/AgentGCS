"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Plus, Save, Trash2, X } from "lucide-react";

import { PersonaRadar } from "@/components/persona-radar";
import { PerfTrace } from "@/components/perf-trace";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiKeyMeta,
  ClaudeConnectionStatus,
  PersonaProfile,
  PersonaStats,
  UserSettings
} from "@/lib/types";

type ConnectionSnapshot = {
  claude: ClaudeConnectionStatus;
  school_api: { token_saved: boolean };
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

function checkboxRow(
  label: string,
  checked: boolean,
  onToggle: (value: boolean) => void
) {
  return (
    <label className="flex items-center justify-between rounded-xl border border-white/70 bg-white/45 px-3 py-2 text-sm text-orange-900/85">
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
  const [statusText, setStatusText] = useState<string | null>(null);
  const [draftSettings, setDraftSettings] = useState<UserSettings>(settings);
  const [showRadar, setShowRadar] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDraftSettings(settings);
    setStatusText(null);
  }, [open, settings]);

  useEffect(() => {
    if (!open) {
      setShowRadar(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setShowRadar(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  const activePersona = useMemo(() => {
    const target = draftSettings.personas.find(
      (persona) => persona.id === draftSettings.active_persona_id
    );
    return target ?? draftSettings.personas[0] ?? null;
  }, [draftSettings.active_persona_id, draftSettings.personas]);

  const updatePersona = useCallback((personaId: string, patch: Partial<PersonaProfile>) => {
    setDraftSettings((current) => ({
      ...current,
      personas: current.personas.map((persona) =>
        persona.id === personaId ? { ...persona, ...patch } : persona
      )
    }));
  }, []);

  function updateActiveStats(nextStats: PersonaStats) {
    if (!activePersona) return;
    updatePersona(activePersona.id, { stats: nextStats });
  }

  function addPersona() {
    const newPersona: PersonaProfile = {
      id: crypto.randomUUID(),
      name: `새 페르소나 ${draftSettings.personas.length + 1}`,
      stats: { ...EMPTY_STATS }
    };
    setDraftSettings({
      ...draftSettings,
      personas: [...draftSettings.personas, newPersona],
      active_persona_id: newPersona.id
    });
  }

  function removePersona(personaId: string) {
    if (draftSettings.personas.length <= 1) return;
    const filtered = draftSettings.personas.filter((persona) => persona.id !== personaId);
    setDraftSettings({
      ...draftSettings,
      personas: filtered,
      active_persona_id: filtered[0]?.id ?? null
    });
  }

  async function handleSaveSettings() {
    setSaving(true);
    setStatusText(null);
    try {
      await onSaveSettings(draftSettings);
      setStatusText("설정이 저장되었습니다.");
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveToken() {
    if (!schoolApiToken.trim()) return;
    setSavingKey(true);
    setStatusText(null);
    try {
      await onSaveApiKey("school_api_token", schoolApiToken.trim());
      setSchoolApiToken("");
      setStatusText("api.1000.school 토큰이 저장되었습니다.");
      await onRefreshConnectionStatus();
    } catch (error) {
      setStatusText((error as Error).message);
    } finally {
      setSavingKey(false);
    }
  }

  const keyNames = apiKeys.map((key) => key.key_name);

  if (!open) return null;

  return (
    <PerfTrace id="settings-modal" thresholdMs={8}>
      <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/35 p-4">
        <div
          className="max-h-[92vh] w-full max-w-5xl overflow-y-auto rounded-3xl border border-white/70 bg-white/95 p-4 shadow-2xl md:p-6 dark:border-slate-700 dark:bg-slate-900/95"
          style={{ contain: "layout paint" }}
        >
        <header className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-gray-800">계정 및 API 설정</h2>
            <p className="text-sm text-orange-900/70">
              user_id: <span className="font-mono">{userId}</span>
              {userEmail ? ` / ${userEmail}` : " / 이메일 미연결"}
            </p>
          </div>
          <Button type="button" variant="secondary" onClick={onClose} className="gap-1.5">
            <X className="h-4 w-4" />
            닫기
          </Button>
        </header>

        <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
          <div className="space-y-4">
            <Card className="space-y-3">
              <CardTitle>색상 테마</CardTitle>
              <CardDescription>Light / Dark / System</CardDescription>
              <ThemeToggle
                value={draftSettings.theme}
                onChange={(next) => setDraftSettings((current) => ({ ...current, theme: next }))}
              />
            </Card>

            <Card className="space-y-3">
              <CardTitle>API 연결</CardTitle>
              <CardDescription>교내 토큰은 Claude 연결에도 동일 토큰으로 사용됩니다.</CardDescription>
              <Input
                value={schoolApiToken}
                onChange={(event) => setSchoolApiToken(event.target.value)}
                placeholder="api.1000.school 토큰 입력"
              />
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="accent"
                  className="gap-1.5"
                  disabled={savingKey || !schoolApiToken.trim()}
                  onClick={handleSaveToken}
                >
                  <Save className="h-4 w-4" />
                  토큰 저장
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => void onRefreshConnectionStatus()}
                >
                  연결 진단 갱신
                </Button>
              </div>
              <div className="rounded-xl border border-white/70 bg-white/50 p-3 text-xs text-orange-900/80">
                저장된 키: {keyNames.length > 0 ? keyNames.join(", ") : "없음"}
              </div>
              <div className="rounded-xl border border-white/70 bg-white/50 p-3 text-xs text-orange-900/80">
                Claude 상태: {connectionStatus?.claude.status ?? "unknown"} /{" "}
                {connectionStatus?.claude.reachable ? "reachable" : "not reachable"}
              </div>
            </Card>

            <Card className="space-y-3">
              <CardTitle>AI 승인 정책</CardTitle>
              <CardDescription>실행 모드별 사용자 승인 요구 여부를 설정합니다.</CardDescription>
              <div className="space-y-2">
                {checkboxRow(
                  "신중함 모드 실행 전 승인",
                  draftSettings.approval_policy.cautious_requires_approval,
                  (next) =>
                    setDraftSettings({
                      ...draftSettings,
                      approval_policy: {
                        ...draftSettings.approval_policy,
                        cautious_requires_approval: next
                      }
                    })
                )}
                {checkboxRow(
                  "균형형 모드 실행 전 승인",
                  draftSettings.approval_policy.balanced_requires_approval,
                  (next) =>
                    setDraftSettings({
                      ...draftSettings,
                      approval_policy: {
                        ...draftSettings.approval_policy,
                        balanced_requires_approval: next
                      }
                    })
                )}
                {checkboxRow(
                  "창의적 모드 실행 전 승인",
                  draftSettings.approval_policy.creative_requires_approval,
                  (next) =>
                    setDraftSettings({
                      ...draftSettings,
                      approval_policy: {
                        ...draftSettings.approval_policy,
                        creative_requires_approval: next
                      }
                    })
                )}
                {checkboxRow(
                  "완전자율 최초 경고/승인 필요",
                  draftSettings.approval_policy.autonomous_needs_first_warning,
                  (next) =>
                    setDraftSettings({
                      ...draftSettings,
                      approval_policy: {
                        ...draftSettings.approval_policy,
                        autonomous_needs_first_warning: next
                      }
                    })
                )}
              </div>
              <p className="rounded-xl border border-amber-200/80 bg-amber-50/70 px-3 py-2 text-xs text-amber-900/80">
                완전자율은 최초 경고 승인 후에는 자동 진행됩니다.
              </p>
            </Card>
          </div>

          <div className="space-y-4">
            <Card className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <CardTitle>에이전트 페르소나</CardTitle>
                  <CardDescription>추가/삭제 후 6축 성향을 조정할 수 있습니다.</CardDescription>
                </div>
                <Button type="button" variant="secondary" className="gap-1.5" onClick={addPersona}>
                  <Plus className="h-4 w-4" />
                  페르소나 추가
                </Button>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {draftSettings.personas.map((persona) => (
                  <div
                    key={persona.id}
                    className={`rounded-xl border px-3 py-2 ${
                      draftSettings.active_persona_id === persona.id
                        ? "border-orange-300 bg-orange-50/70"
                        : "border-white/70 bg-white/45"
                    }`}
                  >
                    <button
                      type="button"
                      className="w-full text-left"
                      onClick={() => setDraftSettings({ ...draftSettings, active_persona_id: persona.id })}
                    >
                      <p className="text-sm font-semibold text-gray-800">{persona.name}</p>
                      <p className="text-xs text-orange-900/65">{persona.id}</p>
                    </button>
                    <div className="mt-2 flex items-center gap-2">
                      <Input
                        value={persona.name}
                        onChange={(event) => updatePersona(persona.id, { name: event.target.value })}
                        placeholder="페르소나 이름"
                        className="h-9 text-xs"
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={() => removePersona(persona.id)}
                        disabled={draftSettings.personas.length <= 1}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            {activePersona && showRadar ? (
              <PersonaRadar value={activePersona.stats} onChange={updateActiveStats} showHeader={false} />
            ) : (
              <Card className="space-y-2">
                <CardTitle>{activePersona ? "페르소나 로딩 중" : "페르소나 없음"}</CardTitle>
                <CardDescription>
                  {activePersona
                    ? "차트를 준비하는 중입니다."
                    : "페르소나를 하나 이상 추가해주세요."}
                </CardDescription>
              </Card>
            )}

            <Card className="space-y-3">
              <CardTitle>고급 설정</CardTitle>
              <CardDescription>Claude 우회 URL, 모델, 기본 이메일 설정</CardDescription>
              <Input
                value={draftSettings.claude_base_url ?? ""}
                onChange={(event) =>
                  setDraftSettings((current) => ({ ...current, claude_base_url: event.target.value }))
                }
                placeholder="Claude Base URL"
              />
              <Input
                value={draftSettings.preferred_model ?? ""}
                onChange={(event) =>
                  setDraftSettings((current) => ({ ...current, preferred_model: event.target.value }))
                }
                placeholder="선호 모델명"
              />
              <Input
                value={draftSettings.default_notify_email ?? ""}
                onChange={(event) =>
                  setDraftSettings((current) => ({ ...current, default_notify_email: event.target.value }))
                }
                placeholder="기본 알림 이메일"
              />
            </Card>

            {statusText && (
              <div className="rounded-xl border border-white/70 bg-white/55 px-3 py-2 text-sm text-orange-900/85">
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
                onClick={handleSaveSettings}
              >
                <Save className="h-4 w-4" />
                설정 저장
              </Button>
            </div>
          </div>
        </div>

        {connectionStatus && connectionStatus.claude.status === "upstream_502" && (
          <div className="mt-4 flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50/90 p-3 text-sm text-amber-900/90">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              `claude.1000.school` 게이트웨이에서 현재 502 응답이 반복되고 있습니다. 토큰 형식은
              인식되지만 업스트림 응답이 실패하므로, 운영 환경에서는 관리자가 게이트웨이 상태를
              점검해야 합니다.
            </p>
          </div>
        )}
        </div>
      </div>
    </PerfTrace>
  );
});
