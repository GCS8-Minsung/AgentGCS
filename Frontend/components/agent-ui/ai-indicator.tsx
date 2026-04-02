"use client";

import { memo, useId, useMemo, useState } from "react";

import { cn } from "@/lib/utils";

interface AIIndicatorProps {
  isActive: boolean;
  isChatStarted?: boolean;
  suggestedOptions?: string[];
  onOptionSelect?: (text: string) => void;
}

const CAPSULE_PATH =
  "M 30,100 C 30,40 85,40 110,100 C 135,160 165,160 190,100 C 215,40 270,40 270,100 C 270,160 215,160 190,100 C 165,40 135,40 110,100 C 85,160 30,160 30,100 Z";

const DEFAULT_STARTERS = [
  "시장 검증 관점으로 과제를 재정의해줘",
  "비즈니스 모델 3개 옵션으로 비교해줘",
  "90일 실행 로드맵을 먼저 제안해줘",
  "핵심 KPI와 리스크를 도출해줘"
];

const DEFAULT_NEXT_STEPS = [
  "방금 제안의 근거를 더 자세히 설명해줘",
  "다른 전략 대안도 같이 보여줘",
  "토론 결과를 요약해서 실행안으로 변환해줘"
];

function AIIndicatorComponent({
  isActive,
  isChatStarted = false,
  suggestedOptions,
  onOptionSelect
}: AIIndicatorProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const gradientId = useId();

  const options = useMemo(() => {
    const fallback = isChatStarted ? DEFAULT_NEXT_STEPS : DEFAULT_STARTERS;
    const source =
      isChatStarted && suggestedOptions && suggestedOptions.length > 0 ? suggestedOptions : fallback;
    return source.slice(0, 5);
  }, [isChatStarted, suggestedOptions]);

  const loopClass = isActive ? "capsule-loop-fast" : isHovered ? "capsule-loop-slow" : "";

  return (
    <div
      className="relative flex w-[240px] flex-col items-center"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => {
        setIsHovered(false);
        setIsMenuOpen(false);
      }}
    >
      <button
        type="button"
        aria-label="빠른 실행 옵션 열기"
        aria-expanded={isMenuOpen}
        onClick={() => setIsMenuOpen((prev) => !prev)}
        className="group h-[90px] w-[220px] cursor-pointer rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-300"
      >
        <svg
          viewBox="0 0 300 200"
          className={cn(
            "h-full w-full transition-transform duration-300",
            isActive ? "scale-[1.01]" : "scale-100"
          )}
          style={{ filter: "drop-shadow(0 8px 16px rgba(255, 200, 170, 0.12))" }}
        >
          <defs>
            <linearGradient id={gradientId} x1="100%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="rgba(255, 179, 0, 0)" />
              <stop offset="50%" stopColor="#FFB300" />
              <stop offset="100%" stopColor="rgba(255, 179, 0, 0)" />
            </linearGradient>
          </defs>
          <path
            d={CAPSULE_PATH}
            fill="none"
            stroke="rgba(255, 255, 255, 0.74)"
            strokeWidth="13"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d={CAPSULE_PATH}
            fill="none"
            stroke={`url(#${gradientId})`}
            strokeWidth="6"
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={100}
            className={cn("capsule-loop", loopClass)}
          />
        </svg>
      </button>

      <div
        className={cn(
          "pointer-events-none absolute top-[92px] z-30 w-[320px] rounded-2xl border border-white/65 bg-white/88 p-2 shadow-xl transition-all duration-150 dark:border-slate-700 dark:bg-slate-900/92",
          isMenuOpen ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0"
        )}
      >
        <div className="grid gap-1">
          {options.map((option, index) => (
            <button
              key={`${option}-${index}`}
              type="button"
              onClick={() => {
                setIsMenuOpen(false);
                onOptionSelect?.(option);
              }}
              className="pointer-events-auto rounded-xl border border-white/70 bg-white/70 px-3 py-2 text-left text-xs text-orange-900/85 transition-colors hover:bg-orange-50/80 hover:text-orange-700 dark:border-slate-600 dark:bg-slate-800/80 dark:text-slate-200 dark:hover:bg-slate-700/90"
            >
              {option}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export const AIIndicator = memo(
  AIIndicatorComponent,
  (prev, next) =>
    prev.isActive === next.isActive &&
    prev.isChatStarted === next.isChatStarted &&
    prev.onOptionSelect === next.onOptionSelect &&
    prev.suggestedOptions === next.suggestedOptions
);
