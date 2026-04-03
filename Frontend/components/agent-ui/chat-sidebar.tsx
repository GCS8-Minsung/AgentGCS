"use client";

import { Clock3, MessageSquare, Plus, Settings, User } from "lucide-react";

export interface SidebarItem {
  id: string;
  title: string;
  subtitle: string;
}

export interface ConversationPreview {
  id: string;
  title: string;
  updated_at?: string;
}

interface ChatSidebarProps {
  items: SidebarItem[];
  activeItem: string | null;
  onSelectItem: (id: string) => void;
  onReset: () => void;
  conversations: ConversationPreview[];
  activeConversationId: string | null;
  onSelectConversation: (conversationId: string) => void;
  onOpenSettings: () => void;
}

export function ChatSidebar({
  items,
  activeItem,
  onSelectItem,
  onReset,
  conversations,
  activeConversationId,
  onSelectConversation,
  onOpenSettings
}: ChatSidebarProps) {
  return (
    <aside
      className="hidden h-full w-80 flex-col gap-6 p-6 lg:flex"
      style={{
        background:
          "linear-gradient(180deg, rgba(255, 255, 255, 0.65), rgba(255, 255, 255, 0.45))",
        borderRight: "1px solid rgba(255, 255, 255, 0.9)",
        boxShadow: "8px 0 24px rgba(255, 160, 122, 0.08)"
      }}
    >
      <button
        onClick={onReset}
        className="flex items-center gap-3 rounded-2xl px-5 py-4 shadow-sm transition-all hover:scale-[1.02]"
        style={{
          background:
            "linear-gradient(135deg, rgba(255, 193, 7, 0.2), rgba(255, 160, 122, 0.15))",
          border: "1px solid rgba(255, 255, 255, 0.8)",
          boxShadow:
            "0 4px 15px rgba(255, 160, 122, 0.1), inset 0 0 10px rgba(255, 255, 255, 0.5)",
          color: "#d97706"
        }}
      >
        <Plus className="h-5 w-5" />
        <span className="text-sm font-semibold tracking-wide">새 워크플로우 시작</span>
      </button>

      <div className="space-y-3">
        {items.map((item) => (
          <button
            key={item.id}
            onClick={() => onSelectItem(item.id)}
            className="group w-full rounded-2xl px-5 py-4 text-left transition-all"
            style={{
              background:
                activeItem === item.id
                  ? "rgba(255, 255, 255, 0.9)"
                  : "rgba(255, 255, 255, 0.4)",
              border: "1px solid rgba(255, 255, 255, 0.8)",
              boxShadow:
                activeItem === item.id
                  ? "0 4px 20px rgba(255, 160, 122, 0.15)"
                  : "0 2px 10px rgba(0,0,0,0.02)"
            }}
          >
            <div className="flex items-start gap-3">
              <MessageSquare
                className="mt-0.5 h-5 w-5 shrink-0 transition-colors"
                style={{
                  color: activeItem === item.id ? "#FFC107" : "#FFA07A"
                }}
              />
              <div className="min-w-0 flex-1">
                <p
                  className={`truncate text-sm font-medium ${
                    activeItem === item.id
                      ? "text-gray-800"
                      : "text-gray-600 group-hover:text-gray-800"
                  }`}
                >
                  {item.title}
                </p>
                <p className="mt-1 text-[11px] font-medium opacity-60">{item.subtitle}</p>
              </div>
            </div>
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-t border-white/50 pt-4">
        <p className="mb-2 px-1 text-xs font-semibold tracking-wide text-orange-900/55">이전 대화</p>
        <div className="space-y-2 pr-2 [&::-webkit-scrollbar]:w-1 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-orange-200/50 [&::-webkit-scrollbar-track]:bg-transparent">
          {conversations.length === 0 && (
            <div className="rounded-xl border border-white/70 bg-white/40 px-3 py-2 text-xs text-orange-900/60">
              아직 저장된 대화가 없습니다.
            </div>
          )}
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              onClick={() => onSelectConversation(conversation.id)}
              className={`w-full rounded-xl border px-3 py-2 text-left transition-all ${
                activeConversationId === conversation.id
                  ? "border-orange-300 bg-white/90"
                  : "border-white/70 bg-white/45 hover:bg-white/70"
              }`}
            >
              <p className="truncate text-xs font-semibold text-gray-800">{conversation.title}</p>
              <p className="mt-1 flex items-center gap-1 text-[10px] text-orange-900/55">
                <Clock3 className="h-3 w-3" />
                {conversation.updated_at
                  ? new Date(conversation.updated_at).toLocaleString()
                  : "시간 정보 없음"}
              </p>
            </button>
          ))}
        </div>
      </div>

      <div className="relative z-20 mt-auto border-t border-white/50 pt-5">
        <button
          type="button"
          onClick={onOpenSettings}
          className="group w-full rounded-2xl px-3 py-3.5 transition-all hover:bg-white/50"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full border border-white bg-gradient-to-br from-orange-100 to-orange-200 text-orange-600 shadow-sm">
                <User className="h-5 w-5" />
              </div>
              <div className="text-left">
                <p className="text-[13px] font-bold tracking-tight text-gray-800">AgentGCS 사용자</p>
                <p className="text-[11px] font-medium text-orange-900/60">계정 및 API 설정</p>
              </div>
            </div>
            <div className="rounded-xl p-2 transition-colors group-hover:bg-white/60">
              <Settings className="h-5 w-5 text-orange-900/40 transition-colors group-hover:text-orange-500" />
            </div>
          </div>
        </button>
      </div>
    </aside>
  );
}
