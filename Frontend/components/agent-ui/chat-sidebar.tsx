"use client";

import { MessageSquare, Plus, Settings, User } from "lucide-react";
import { motion } from "motion/react";

export interface SidebarItem {
  id: string;
  title: string;
  subtitle: string;
}

interface ChatSidebarProps {
  items: SidebarItem[];
  activeItem: string | null;
  onSelectItem: (id: string) => void;
  onReset: () => void;
}

export function ChatSidebar({
  items,
  activeItem,
  onSelectItem,
  onReset
}: ChatSidebarProps) {
  return (
    <motion.aside
      initial={{ x: -300, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      className="hidden h-full w-80 flex-col gap-6 p-6 lg:flex"
      style={{
        background:
          "linear-gradient(180deg, rgba(255, 255, 255, 0.65), rgba(255, 255, 255, 0.45))",
        backdropFilter: "blur(30px)",
        WebkitBackdropFilter: "blur(30px)",
        borderRight: "1px solid rgba(255, 255, 255, 0.9)",
        boxShadow: "10px 0 40px rgba(255, 160, 122, 0.08)"
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

      <div className="flex-1 space-y-3 overflow-y-auto pr-2 [&::-webkit-scrollbar]:w-1 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-orange-200/50 [&::-webkit-scrollbar-track]:bg-transparent">
        {items.map((item) => (
          <motion.button
            key={item.id}
            onClick={() => onSelectItem(item.id)}
            whileHover={{ scale: 1.02, x: 5 }}
            whileTap={{ scale: 0.98 }}
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
          </motion.button>
        ))}
      </div>

      <div className="relative z-20 mt-auto border-t border-white/50 pt-5">
        <button
          className="group w-full rounded-2xl px-3 py-3.5 transition-all hover:bg-white/50"
          style={{
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)"
          }}
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
    </motion.aside>
  );
}

