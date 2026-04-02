"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer
} from "recharts";

import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PERSONA_AXES, PersonaStats } from "@/lib/types";

type Props = {
  value: PersonaStats;
  onChange: (next: PersonaStats) => void;
};

const SIZE = 320;
const CENTER = SIZE / 2;
const RADIUS = 112;

export function PersonaRadar({ value, onChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [activeAxisIndex, setActiveAxisIndex] = useState<number | null>(null);

  const data = useMemo(
    () => PERSONA_AXES.map((axis) => ({ axis: axis.label, value: value[axis.key] })),
    [value]
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
        const ratio = value[axis.key] / 100;
        return {
          x: CENTER + axisVectors[index].x * RADIUS * ratio,
          y: CENTER + axisVectors[index].y * RADIUS * ratio
        };
      }),
    [axisVectors, value]
  );

  const updateAxisByPointer = useCallback(
    (axisIndex: number, clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const x = ((clientX - rect.left) / rect.width) * SIZE;
      const y = ((clientY - rect.top) / rect.height) * SIZE;
      const dx = x - CENTER;
      const dy = y - CENTER;

      const unit = axisVectors[axisIndex];
      const projected = (dx * unit.x + dy * unit.y) / RADIUS;
      const nextValue = Math.round(Math.max(0, Math.min(1, projected)) * 100);
      const targetAxis = PERSONA_AXES[axisIndex];
      onChange({ ...value, [targetAxis.key]: nextValue });
    },
    [axisVectors, onChange, value]
  );

  useEffect(() => {
    if (activeAxisIndex === null) return;
    const move = (event: PointerEvent) => {
      updateAxisByPointer(activeAxisIndex, event.clientX, event.clientY);
    };
    const up = () => setActiveAxisIndex(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [activeAxisIndex, updateAxisByPointer]);

  return (
    <Card className="space-y-4">
      <div>
        <CardTitle>Persona Control (6축 헥사곤)</CardTitle>
        <CardDescription>
          각 점을 드래그해서 성향(0~100)을 조정하고 JSON으로 백엔드에 전달합니다.
        </CardDescription>
      </div>

      <div className="relative mx-auto h-[320px] w-[320px]">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart
            data={data}
            cx="50%"
            cy="50%"
            outerRadius={112}
            startAngle={90}
            endAngle={-270}
          >
            <PolarGrid stroke="#fb923c" strokeOpacity={0.35} />
            <PolarAngleAxis dataKey="axis" tick={{ fill: "#7c2d12", fontSize: 12 }} />
            <Radar
              dataKey="value"
              fill="#fbbf24"
              fillOpacity={0.35}
              stroke="#f97316"
              strokeWidth={2}
            />
          </RadarChart>
        </ResponsiveContainer>

        <svg
          ref={svgRef}
          width={SIZE}
          height={SIZE}
          className="absolute inset-0 h-full w-full touch-none"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
        >
          {handlePoints.map((point, index) => (
            <g key={PERSONA_AXES[index].key}>
              <circle cx={point.x} cy={point.y} r={11} fill="#7c2d12" fillOpacity={0.12} />
              <circle
                cx={point.x}
                cy={point.y}
                r={8}
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

      <div className="grid grid-cols-2 gap-2">
        {PERSONA_AXES.map((axis) => (
          <label key={axis.key} className="space-y-1 text-xs text-orange-900/80">
            <span className="font-semibold">{axis.label}</span>
            <Input
              type="number"
              min={0}
              max={100}
              value={value[axis.key]}
              onChange={(event) => {
                const numeric = Number(event.target.value);
                const clamped = Number.isFinite(numeric)
                  ? Math.max(0, Math.min(100, numeric))
                  : 0;
                onChange({ ...value, [axis.key]: clamped });
              }}
            />
          </label>
        ))}
      </div>

      <pre className="overflow-x-auto rounded-2xl bg-gradient-to-r from-orange-50 to-amber-50 p-3 text-xs text-orange-900">
        {JSON.stringify(value, null, 2)}
      </pre>
    </Card>
  );
}
