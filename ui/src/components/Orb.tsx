import { useEffect, useRef } from "react";
import { createOrb, type OrbHandle, type OrbState } from "../orb";

// React wrapper around the framework-agnostic Three.js orb. The imperative
// createOrb() handle (setState/setAnalyser/destroy) is created once and driven by
// props — React never re-renders the canvas, it just forwards state changes.
export function Orb({
  state,
  analyser,
  className,
}: {
  state: OrbState;
  analyser: AnalyserNode | null;
  /** Extra CSS class (e.g. orb-compact for chat-header presence indicator). */
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const orbRef = useRef<OrbHandle | null>(null);

  // Create once on mount, destroy on unmount.
  useEffect(() => {
    if (!canvasRef.current) return;
    const orb = createOrb(canvasRef.current);
    orbRef.current = orb;
    return () => {
      orb.destroy();
      orbRef.current = null;
    };
  }, []);

  // Forward state + analyser changes to the running orb.
  useEffect(() => {
    orbRef.current?.setState(state);
  }, [state]);
  useEffect(() => {
    orbRef.current?.setAnalyser(analyser);
  }, [analyser]);

  return <canvas ref={canvasRef} className={`orb-canvas${className ? ` ${className}` : ""}`} />;
}
