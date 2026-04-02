"use client";

import { motion } from "motion/react";
import { Sparkles, User } from "lucide-react";

interface ChatMessageProps {
  content: string;
  role: "user" | "assistant";
  timestamp: Date;
}

export function ChatMessage({ content, role, timestamp }: ChatMessageProps) {
  const isUser = role === "user";

  return (
    <motion.article
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex gap-4 ${isUser ? "justify-end" : "justify-start"}`}
    >
      {!isUser && (
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full shadow-sm"
          style={{
            background:
              "linear-gradient(135deg, rgba(255, 193, 7, 0.25), rgba(255, 160, 122, 0.2))",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            border: "1px solid rgba(255, 255, 255, 0.9)",
            boxShadow:
              "0 4px 15px rgba(255, 193, 7, 0.15), inset 0 0 10px rgba(255, 255, 255, 0.8)"
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
          backdropFilter: "blur(24px)",
          WebkitBackdropFilter: "blur(24px)",
          border: "1px solid rgba(255, 255, 255, 0.8)",
          boxShadow: isUser
            ? "0 4px 20px rgba(255, 160, 122, 0.1), inset 0 0 15px rgba(255, 255, 255, 0.6)"
            : "0 4px 20px rgba(0, 0, 0, 0.03), inset 0 0 15px rgba(255, 255, 255, 0.9)",
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
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            border: "1px solid rgba(255, 255, 255, 0.9)",
            boxShadow:
              "0 4px 15px rgba(255, 160, 122, 0.08), inset 0 0 10px rgba(255, 255, 255, 0.9)"
          }}
        >
          <User className="h-5 w-5" style={{ color: "#d97706" }} />
        </div>
      )}
    </motion.article>
  );
}

