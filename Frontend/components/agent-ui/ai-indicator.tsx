"use client";

import { useEffect, useState } from "react";
import {
  AnimatePresence,
  animate,
  motion,
  useAnimationFrame,
  useMotionValue
} from "motion/react";

interface AIIndicatorProps {
  isActive: boolean;
  isChatStarted?: boolean;
  suggestedOptions?: string[];
  onOptionSelect?: (text: string) => void;
}

export function AIIndicator({
  isActive,
  isChatStarted = false,
  suggestedOptions,
  onOptionSelect
}: AIIndicatorProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  const defaultStarters = [
    "시장 검증 관점으로 과제를 재정의해줘",
    "비즈니스 모델 3개 옵션으로 비교해줘",
    "90일 실행 로드맵을 먼저 제안해줘",
    "핵심 KPI와 리스크를 도출해줘"
  ];

  const defaultNextSteps = [
    "방금 제안의 근거를 더 자세히 설명해줘",
    "다른 전략 대안도 같이 보여줘",
    "토론 결과를 요약해서 실행안으로 변환해줘"
  ];

  const displayOptions =
    isChatStarted && suggestedOptions && suggestedOptions.length > 0
      ? suggestedOptions
      : isChatStarted
        ? defaultNextSteps
        : defaultStarters;
  const finalOptions = displayOptions.slice(0, 5);

  const speedMult = useMotionValue(isActive ? 1.5 : 0.3);
  const offset1 = useMotionValue(100);
  const offset2 = useMotionValue(100);
  const offset3 = useMotionValue(100);
  const offset4 = useMotionValue(100);

  useEffect(() => {
    animate(speedMult, isActive ? 1.5 : 0.3, {
      duration: isActive ? 0.3 : 4,
      ease: "easeOut"
    });
  }, [isActive, speedMult]);

  useAnimationFrame((_time, delta) => {
    const mult = speedMult.get();
    const dSec = delta / 1000;
    const updateOffset = (current: number, baseSpeed: number) => {
      let next = current - baseSpeed * dSec;
      if (next <= 0) next += 100;
      return next;
    };
    offset1.set(updateOffset(offset1.get(), 40 * mult));
    offset2.set(updateOffset(offset2.get(), 30 * mult));
    offset3.set(updateOffset(offset3.get(), 60 * mult));
    offset4.set(updateOffset(offset4.get(), 140 * mult));
  });

  const pathD =
    "M 30,100 C 30,40 85,40 110,100 C 135,160 165,160 190,100 C 215,40 270,40 270,100 C 270,160 215,160 190,100 C 165,40 135,40 110,100 C 85,160 30,160 30,100 Z";

  return (
    <div
      className="group relative flex h-[90px] w-[220px] cursor-pointer items-center justify-center"
      onMouseEnter={() => setIsMenuOpen(true)}
      onMouseLeave={() => setIsMenuOpen(false)}
      onClick={() => setIsMenuOpen((prev) => !prev)}
    >
      <AnimatePresence>
        {isMenuOpen && (
          <div className="pointer-events-none absolute left-1/2 top-1/2 z-50 h-0 w-0">
            {finalOptions.map((option, index) => {
              const total = finalOptions.length;
              let angle = 90;
              if (total > 1) {
                const startAngle = 160;
                const endAngle = 20;
                angle = startAngle - (index * (startAngle - endAngle)) / (total - 1);
              }
              const rad = (angle * Math.PI) / 180;
              const radius = 175;
              const lineWidth = radius - 55;
              return (
                <div key={`${option}-${index}`} className="pointer-events-none absolute left-0 top-0">
                  <div className="absolute left-0 top-0 h-0 w-0" style={{ transform: `rotate(${-angle}deg)` }}>
                    <motion.div
                      initial={{ opacity: 0, scaleX: 0 }}
                      animate={{ opacity: 1, scaleX: 1 }}
                      exit={{ opacity: 0, scaleX: 0 }}
                      transition={{ duration: 0.3, delay: index * 0.05, ease: "easeOut" }}
                      className="absolute origin-left"
                      style={{
                        top: "-1px",
                        left: "45px",
                        width: `${lineWidth}px`,
                        height: "2px",
                        background:
                          "linear-gradient(to right, rgba(255, 179, 0, 0) 0%, rgba(255, 179, 0, 0.9) 100%)",
                        boxShadow: "0 0 10px rgba(255, 179, 0, 0.4)"
                      }}
                    />
                  </div>
                  <motion.button
                    initial={{ opacity: 0, scale: 0.3, x: "-50%", y: "-50%" }}
                    animate={{
                      opacity: 1,
                      scale: 1,
                      x: `calc(-50% + ${Math.cos(rad) * radius}px)`,
                      y: `calc(-50% - ${Math.sin(rad) * radius}px)`
                    }}
                    exit={{ opacity: 0, scale: 0.3, x: "-50%", y: "-50%" }}
                    transition={{
                      type: "spring",
                      damping: 18,
                      stiffness: 220,
                      mass: 0.8,
                      delay: index * 0.05
                    }}
                    onClick={(event) => {
                      event.stopPropagation();
                      setIsMenuOpen(false);
                      onOptionSelect?.(option);
                    }}
                    className="pointer-events-auto absolute flex min-h-[110px] min-w-[110px] max-w-[150px] cursor-pointer items-center justify-center rounded-[40px] px-4 py-3 text-center text-[11px] font-normal tracking-wide text-orange-900/60 transition-all hover:scale-105 hover:bg-white/40 hover:text-orange-500"
                    style={{
                      background: "rgba(255, 255, 255, 0.1)",
                      backdropFilter: "blur(12px)",
                      WebkitBackdropFilter: "blur(12px)",
                      border: "1px solid rgba(255, 255, 255, 0.25)",
                      boxShadow:
                        "0 8px 32px rgba(255, 160, 122, 0.05), inset 0 0 15px rgba(255, 255, 255, 0.15)",
                      width: "max-content",
                      lineHeight: "1.4",
                      wordBreak: "keep-all"
                    }}
                  >
                    <span>{option}</span>
                  </motion.button>
                </div>
              );
            })}
          </div>
        )}
      </AnimatePresence>

      <motion.svg
        viewBox="0 0 300 200"
        className="h-full w-full"
        style={{ filter: "drop-shadow(0px 8px 16px rgba(255, 200, 170, 0.15))" }}
        animate={{
          scale: isActive ? [1, 1.05, 1] : [1, 1.02, 1],
          rotate: isActive ? [0, 1, -1, 0] : [0, 0.5, -0.5, 0]
        }}
        transition={{
          duration: isActive ? 3 : 6,
          repeat: Infinity,
          ease: "easeInOut"
        }}
      >
        <defs>
          <filter id="insetShadow">
            <feOffset dx="2" dy="2" />
            <feGaussianBlur stdDeviation="2" result="offset-blur" />
            <feComposite operator="out" in="SourceGraphic" in2="offset-blur" result="inverse" />
            <feFlood floodColor="rgba(255, 180, 150, 0.25)" result="color" />
            <feComposite operator="in" in="color" in2="inverse" result="shadow" />
            <feMerge>
              <feMergeNode in="SourceGraphic" />
              <feMergeNode in="shadow" />
            </feMerge>
          </filter>
          <filter id="amberGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="2" result="blur1" />
            <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur2" />
            <feMerge>
              <feMergeNode in="blur2" />
              <feMergeNode in="blur1" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <linearGradient id="glowLightOrange" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="rgba(255, 204, 170, 0)" />
            <stop offset="50%" stopColor="#FFCCAA" />
            <stop offset="100%" stopColor="rgba(255, 204, 170, 0)" />
          </linearGradient>
          <linearGradient id="glowAmberGradient" x1="100%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="rgba(255, 179, 0, 0)" />
            <stop offset="50%" stopColor="#FFB300" />
            <stop offset="100%" stopColor="rgba(255, 179, 0, 0)" />
          </linearGradient>
          <linearGradient id="glowWhiteGradient" x1="0%" y1="100%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgba(255, 255, 255, 0)" />
            <stop offset="50%" stopColor="#FFFFFF" />
            <stop offset="100%" stopColor="rgba(255, 255, 255, 0)" />
          </linearGradient>
        </defs>

        <path
          d={pathD}
          fill="none"
          stroke="rgba(255, 255, 255, 0.7)"
          strokeWidth="14"
          strokeLinecap="round"
          strokeLinejoin="round"
          filter="url(#insetShadow)"
        />
        <motion.path
          d={pathD}
          fill="none"
          stroke="url(#glowLightOrange)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength="100"
          strokeDasharray="30 70"
          style={{ strokeDashoffset: offset1 }}
        />
        <motion.path
          d={pathD}
          fill="none"
          stroke="url(#glowAmberGradient)"
          strokeWidth="5"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength="100"
          strokeDasharray="20 80"
          filter="url(#amberGlow)"
          style={{ strokeDashoffset: offset2 }}
        />
        <motion.path
          d={pathD}
          fill="none"
          stroke="url(#glowWhiteGradient)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength="100"
          strokeDasharray="10 90"
          style={{ strokeDashoffset: offset3 }}
        />
        <motion.path
          d={pathD}
          fill="none"
          stroke="url(#glowAmberGradient)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength="100"
          strokeDasharray="5 95"
          style={{
            strokeDashoffset: offset4,
            filter: "drop-shadow(0 0 4px #FFB300)",
            opacity: isActive ? 1 : 0,
            transition: "opacity 1.5s ease-out"
          }}
        />
      </motion.svg>
    </div>
  );
}

