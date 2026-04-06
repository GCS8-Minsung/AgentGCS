"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer
} from "recharts";

import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { normalizePersonaStats, PERSONA_AXES, PersonaStats } from "@/lib/types";

type Props = {
  value: PersonaStats;
  onChange: (next: PersonaStats) => void;
  showHeader?: boolean;
  size?: number;
  title?: string;
  description?: string;
  showInputs?: boolean;
};

const HANDLE_HALO_RADIUS = 9;
const HANDLE_RADIUS = 6;

function PersonaRadarComponent({
  value,
  onChange,
  showHeader = true,
  size = 320,
  title = "Persona Control (6축 헥사곤)",
  description = "각 점을 드래그해서 성향(0~100)을 조정합니다.",
  showInputs = true
}: Props) {
  const center = size / 2;
  const baseRadius = size * 0.31;
  const interactionRadius = Math.max(24, baseRadius - HANDLE_HALO_RADIUS - 2);
  const svgRef = useRef<SVGSVGElement>(null);
  const valueRef = useRef<PersonaStats>(value);
  const moveRef = useRef<{ x: number; y: number } | null>(null);
  const rafRef = useRef<number | null>(null);
  const [activeAxisIndex, setActiveAxisIndex] = useState<number | null>(null);
  const normalizedValue = useMemo(() => normalizePersonaStats(value), [value]);

  useEffect(() => {
    valueRef.current = normalizedValue;
  }, [normalizedValue]);

  const data = useMemo(
    () => PERSONA_AXES.map((axis) => ({ axis: axis.label, value: normalizedValue[axis.key] })),
    [normalizedValue]
  );

  const axisVectors = useMemo(
    () =>
      PERSONA_AXES.map((_axis, index) => {
        const angle = (-90 + index * 60) * (Math.PI / 180);
        return { x: Math.cos(angle), y: Math.sin(angle) };
      }),
    []
  );

  const handlePoints = useMemo(
    () =>
      PERSONA_AXES.map((axis, index) => {
        const ratio = normalizedValue[axis.key] / 100;
        return {
          x: center + axisVectors[index].x * interactionRadius * ratio,
          y: center + axisVectors[index].y * interactionRadius * ratio
        };
      }),
    [axisVectors, center, normalizedValue, interactionRadius]
  );

  const updateAxisByPointer = useCallback(
    (axisIndex: number, clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const x = ((clientX - rect.left) / rect.width) * size;
      const y = ((clientY - rect.top) / rect.height) * size;
      const dx = x - center;
      const dy = y - center;

      const unit = axisVectors[axisIndex];
      const projected = (dx * unit.x + dy * unit.y) / interactionRadius;
      const nextValue = Math.round(Math.max(0, Math.min(1, projected)) * 100);
      const targetAxis = PERSONA_AXES[axisIndex];
      const currentValue = valueRef.current[targetAxis.key];
      if (currentValue === nextValue) return;
      onChange({ ...valueRef.current, [targetAxis.key]: nextValue });
    },
    [axisVectors, center, onChange, interactionRadius, size]
  );

  useEffect(() => {
    if (activeAxisIndex === null) return;
    const move = (event: PointerEvent) => {
      moveRef.current = { x: event.clientX, y: event.clientY };
      if (rafRef.current !== null) return;
      rafRef.current = window.requestAnimationFrame(() => {
        const point = moveRef.current;
        if (point) {
          updateAxisByPointer(activeAxisIndex, point.x, point.y);
        }
        rafRef.current = null;
      });
    };
    const up = () => {
      setActiveAxisIndex(null);
      moveRef.current = null;
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [activeAxisIndex, updateAxisByPointer]);

  return (
    <Card className="space-y-4">
      {showHeader && (
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
      )}

      <div className="relative mx-auto" style={{ width: `${size}px`, height: `${size}px` }}>
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart
            data={data}
            cx="50%"
            cy="50%"
            outerRadius={interactionRadius}
            startAngle={90}
            endAngle={-270}
          >
            <PolarGrid stroke="#fb923c" strokeOpacity={0.35} />
            <PolarAngleAxis
              dataKey="axis"
              tick={{ fill: "var(--radar-tick-color)", fontSize: size < 220 ? 10 : 12 }}
            />
            <Radar
              dataKey="value"
              fill="#fbbf24"
              fillOpacity={0.35}
              stroke="#f97316"
              strokeWidth={2}
              isAnimationActive={false}
            />
          </RadarChart>
        </ResponsiveContainer>

        <svg
          ref={svgRef}
          width={size}
          height={size}
          className="absolute inset-0 h-full w-full touch-none"
          viewBox={`0 0 ${size} ${size}`}
        >
          {handlePoints.map((point, index) => (
            <g key={PERSONA_AXES[index].key}>
              <circle cx={point.x} cy={point.y} r={HANDLE_HALO_RADIUS} fill="#7c2d12" fillOpacity={0.12} />
              <circle
                cx={point.x}
                cy={point.y}
                r={HANDLE_RADIUS}
                fill="#f97316"
                stroke="#fff"
                strokeWidth={2}
                className="cursor-grab active:cursor-grabbing"
                onPointerDown={(event) => {
                  event.preventDefault();
                  setActiveAxisIndex(index);
                }}
              />
            </g>
          ))}
        </svg>
      </div>

      {showInputs && (
        <div className="grid grid-cols-2 gap-2">
          {PERSONA_AXES.map((axis) => (
            <label
              key={axis.key}
              className="space-y-1 text-xs text-orange-900/80 dark:text-slate-200"
            >
              <span className="font-semibold">{axis.label}</span>
              <Input
                type="number"
                min={0}
                max={100}
                value={normalizedValue[axis.key]}
                onChange={(event) => {
                  const numeric = Number(event.target.value);
                  const clamped = Number.isFinite(numeric)
                    ? Math.max(0, Math.min(100, numeric))
                    : 0;
                  if (normalizedValue[axis.key] === clamped) return;
                  onChange({ ...normalizedValue, [axis.key]: clamped });
                }}
              />
            </label>
          ))}
        </div>
      )}

    </Card>
  );
}

export const PersonaRadar = memo(PersonaRadarComponent);
