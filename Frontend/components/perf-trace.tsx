"use client";

import { Profiler, ReactNode, useCallback } from "react";

type Props = {
  id: string;
  children: ReactNode;
  thresholdMs?: number;
};

export function PerfTrace({ id, children, thresholdMs = 12 }: Props) {
  const onRender = useCallback(
    (
      profilerId: string,
      phase: "mount" | "update" | "nested-update",
      actualDuration: number,
      baseDuration: number
    ) => {
      if (process.env.NODE_ENV === "production") return;
      if (actualDuration < thresholdMs) return;
      // eslint-disable-next-line no-console
      console.warn(
        `[PerfTrace] ${profilerId} ${phase} actual=${actualDuration.toFixed(
          1
        )}ms base=${baseDuration.toFixed(1)}ms`
      );
    },
    [thresholdMs]
  );

  if (process.env.NODE_ENV === "production") {
    return <>{children}</>;
  }

  return (
    <Profiler id={id} onRender={onRender}>
      {children}
    </Profiler>
  );
}

