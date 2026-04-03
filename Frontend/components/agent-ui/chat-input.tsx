"use client";

import { useState } from "react";
import { Paperclip, Scale, Send, ShieldCheck, Sparkles, Zap } from "lucide-react";

import { AutonomyMode } from "@/lib/types";

interface ChatInputProps {
  onSend: (message: string, autonomyMode: AutonomyMode) => void;
  isCenter: boolean;
  disabled?: boolean;
}

export function ChatInput({ onSend, isCenter, disabled = false }: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [autonomyMode, setAutonomyMode] = useState<AutonomyMode>("balanced");

  const levels = [
    { id: "cautious", label: "신중함", icon: ShieldCheck },
    { id: "balanced", label: "균형형", icon: Scale },
    { id: "creative", label: "창의적", icon: Sparkles },
    { id: "autonomous", label: "완전자율", icon: Zap }
  ];

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!message.trim() || disabled) return;
    onSend(message.trim(), autonomyMode);
    setMessage("");
  }

  return (
    <div className={`${isCenter ? "w-full max-w-2xl" : "w-full max-w-4xl"} px-4`}>
      <form onSubmit={handleSubmit}>
        <div
          className="relative flex items-center gap-3 rounded-3xl px-6 py-4 transition-all"
          style={{
            background: "rgba(255, 255, 255, 0.6)",
            border: "1px solid rgba(255, 255, 255, 0.8)",
            boxShadow: "0 8px 24px rgba(255, 160, 122, 0.12)"
          }}
        >
          <button
            type="button"
            className="mr-1 flex items-center justify-center rounded-2xl p-2.5 text-orange-900/40 transition-all hover:bg-orange-900/5 hover:text-orange-900/70 dark:text-slate-400 dark:hover:bg-slate-800/60 dark:hover:text-slate-100"
            title="파일 첨부"
          >
            <Paperclip className="h-5 w-5" />
          </button>
          <input
            type="text"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            disabled={disabled}
            placeholder={disabled ? "에이전트가 응답 생성 중입니다..." : "AI와 대화할 메시지를 입력하세요..."}
            className={`flex-1 bg-transparent text-gray-800 outline-none placeholder:text-orange-900/40 dark:text-slate-100 dark:placeholder:text-slate-400 ${
              disabled ? "cursor-not-allowed opacity-60" : ""
            }`}
          />
          <button
            type="submit"
            className={`flex items-center justify-center rounded-2xl p-3 transition-all ${
              disabled ? "cursor-not-allowed opacity-40" : ""
            }`}
            style={{
              background:
                message.trim() && !disabled
                  ? "linear-gradient(135deg, #FFC107, #FFA07A)"
                  : "rgba(255, 255, 255, 0.5)",
              border:
                message.trim() && !disabled
                  ? "1px solid rgba(255, 255, 255, 0.4)"
                  : "1px solid rgba(255, 255, 255, 0.6)",
              boxShadow: message.trim() && !disabled ? "0 4px 15px rgba(255, 193, 7, 0.4)" : "none"
            }}
            disabled={!message.trim() || disabled}
          >
            <Send
              className="h-5 w-5"
              style={{
                color: message.trim() && !disabled ? "white" : "rgba(255, 160, 122, 0.6)"
              }}
            />
          </button>
        </div>

        <div className="mt-3 flex justify-center">
          <div
            className="flex items-center gap-1 overflow-x-auto rounded-2xl p-1 [&::-webkit-scrollbar]:hidden"
            style={{
              background: "rgba(255, 255, 255, 0.4)",
              border: "1px solid rgba(255, 255, 255, 0.5)",
              boxShadow: "0 3px 10px rgba(255, 160, 122, 0.05)"
            }}
          >
            {levels.map((level) => {
              const Icon = level.icon;
              const selected = autonomyMode === level.id;
              return (
                <button
                  key={level.id}
                  type="button"
                  onClick={() => setAutonomyMode(level.id as AutonomyMode)}
                  className={`flex items-center gap-1.5 whitespace-nowrap rounded-xl px-3 py-2 text-[12px] font-semibold transition-all ${
                    selected
                      ? "border border-orange-100/50 bg-white text-orange-600 shadow-sm"
                      : "text-gray-500 hover:bg-white/40 hover:text-gray-700 dark:text-slate-300 dark:hover:bg-slate-700/60 dark:hover:text-slate-100"
                  }`}
                >
                  <Icon className={`h-3.5 w-3.5 ${selected ? "text-orange-500" : "text-gray-400"}`} />
                  {level.label}
                </button>
              );
            })}
          </div>
        </div>

        {isCenter && (
          <p className="mt-5 text-center text-[13px] font-medium tracking-wide text-orange-900/50 dark:text-slate-400">
            AgentGCS Multi-Agent Console.
          </p>
        )}
      </form>
    </div>
  );
}
