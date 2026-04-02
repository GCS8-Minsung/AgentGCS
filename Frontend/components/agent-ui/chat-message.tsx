"use client";

import { memo } from "react";
import { Sparkles, User } from "lucide-react";

interface ChatMessageProps {
  content: string;
  role: "user" | "assistant";
  timestamp: Date;
}

function ChatMessageComponent({ content, role, timestamp }: ChatMessageProps) {
  const isUser = role === "user";

  return (
    <article className={`flex gap-4 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full shadow-sm"
          style={{
            background:
              "linear-gradient(135deg, rgba(255, 193, 7, 0.25), rgba(255, 160, 122, 0.2))",
            border: "1px solid rgba(255, 255, 255, 0.9)",
            boxShadow: "0 3px 10px rgba(255, 193, 7, 0.12)"
          }}
        >
          <Sparkles className="h-5 w-5" style={{ color: "#d97706" }} />
        </div>
      )}

      <div
        className="max-w-3xl rounded-3xl px-6 py-4"
        style={{
          background: isUser
            ? "linear-gradient(135deg, rgba(255, 193, 7, 0.1), rgba(255, 160, 122, 0.2))"
            : "rgba(255, 255, 255, 0.7)",
          border: "1px solid rgba(255, 255, 255, 0.8)",
          boxShadow: isUser
            ? "0 4px 14px rgba(255, 160, 122, 0.1)"
            : "0 4px 14px rgba(0, 0, 0, 0.04)",
          color: "#4a4a4a"
        }}
      >
        <p className="whitespace-pre-wrap text-sm leading-relaxed">{content}</p>
        <p className="mt-2 text-right text-[10px] font-medium opacity-50">
          {timestamp.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}
        </p>
      </div>

      {isUser && (
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full shadow-sm"
          style={{
            background: "rgba(255, 255, 255, 0.7)",
            border: "1px solid rgba(255, 255, 255, 0.9)",
            boxShadow: "0 3px 10px rgba(255, 160, 122, 0.08)"
          }}
        >
          <User className="h-5 w-5" style={{ color: "#d97706" }} />
        </div>
      )}
    </article>
  );
}

export const ChatMessage = memo(ChatMessageComponent);
