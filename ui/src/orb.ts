// Audio-reactive particle orb — the visual centerpiece, in the spirit of
// ethanplusai/jarvis's orb.ts. Fully encapsulated and framework-agnostic:
//   const orb = createOrb(canvas);
//   orb.setState("thinking");
//   orb.setAnalyser(analyserNode); // drives audio reactivity
//   orb.destroy();
//
// It knows nothing about React or Ciel — the React <Orb/> wrapper feeds it state
// (idle/listening/thinking/speaking) derived from the real conversation, and an
// AnalyserNode fed by the TTS audio so the orb pulses to Ciel's actual voice.

import * as THREE from "three";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

interface StateTarget {
  radius: number;
  speed: number;      // rotation speed
  motion: number;     // per-particle float speed
  brightness: number; // point opacity
  lineDensity: number; // fraction of precomputed line pairs drawn (0..1)
  lineOpacity: number;
  electron: number;   // electron spawn intensity (0..1)
  color: THREE.Color;
}

const STATE_TARGETS: Record<OrbState, StateTarget> = {
  idle:      { radius: 28, speed: 0.08, motion: 0.5, brightness: 0.35, lineDensity: 0.08, lineOpacity: 0.10, electron: 0.0,  color: new THREE.Color(0x4ca8e8) },
  listening: { radius: 22, speed: 0.16, motion: 0.8, brightness: 0.60, lineDensity: 0.35, lineOpacity: 0.20, electron: 0.0,  color: new THREE.Color(0x4ca8e8) },
  thinking:  { radius: 16, speed: 0.50, motion: 1.6, brightness: 0.85, lineDensity: 1.00, lineOpacity: 0.34, electron: 1.0,  color: new THREE.Color(0x6ec4ff) },
  speaking:  { radius: 18, speed: 0.28, motion: 1.1, brightness: 1.00, lineDensity: 0.60, lineOpacity: 0.30, electron: 0.18, color: new THREE.Color(0x5ab8f0) },
};

const COUNT = 2000;
const MAX_PAIRS = 8000;
const ELECTRON_POOL = 48;

function softDotTexture(): THREE.Texture {
  const size = 64;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.25, "rgba(255,255,255,0.9)");
  g.addColorStop(0.5, "rgba(255,255,255,0.35)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.needsUpdate = true;
  return tex;
}

// Fibonacci sphere → evenly distributed unit directions.
function fibonacciSphere(n: number): Float32Array {
  const out = new Float32Array(n * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = i * golden;
    out[i * 3] = Math.cos(theta) * r;
    out[i * 3 + 1] = y;
    out[i * 3 + 2] = Math.sin(theta) * r;
  }
  return out;
}

// For each particle keep its ~6 nearest neighbours (by angular closeness), collect
// unique pairs, sort closest-first so drawing the first N gives the densest short
// links — that's how per-state "lineDensity" scales the mesh.
function buildPairs(dirs: Float32Array): Int32Array {
  const pairs: { a: number; b: number; d: number }[] = [];
  const K = 6;
  for (let i = 0; i < COUNT; i++) {
    const ix = dirs[i * 3], iy = dirs[i * 3 + 1], iz = dirs[i * 3 + 2];
    const best: { j: number; dot: number }[] = [];
    for (let j = i + 1; j < COUNT; j++) {
      const dot = ix * dirs[j * 3] + iy * dirs[j * 3 + 1] + iz * dirs[j * 3 + 2];
      if (best.length < K) {
        best.push({ j, dot });
        if (best.length === K) best.sort((p, q) => p.dot - q.dot);
      } else if (dot > best[0].dot) {
        best[0] = { j, dot };
        best.sort((p, q) => p.dot - q.dot);
      }
    }
    for (const b of best) pairs.push({ a: i, b: b.j, d: b.dot });
  }
  pairs.sort((p, q) => q.d - p.d); // closest (highest dot) first
  const n = Math.min(pairs.length, MAX_PAIRS);
  const out = new Int32Array(n * 2);
  for (let k = 0; k < n; k++) {
    out[k * 2] = pairs[k].a;
    out[k * 2 + 1] = pairs[k].b;
  }
  return out;
}

export interface OrbHandle {
  setState: (s: OrbState) => void;
  setAnalyser: (a: AnalyserNode | null) => void;
  destroy: () => void;
}

export function createOrb(canvas: HTMLCanvasElement): OrbHandle {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
  camera.position.set(0, 0, 78);

  const group = new THREE.Group();
  scene.add(group);

  // ── Particles ──
  const dirs = fibonacciSphere(COUNT);
  const phases = new Float32Array(COUNT);
  const pSize = new Float32Array(COUNT);
  for (let i = 0; i < COUNT; i++) {
    phases[i] = Math.random() * Math.PI * 2;
    pSize[i] = 0.6 + Math.random() * 0.8;
  }
  const positions = new Float32Array(COUNT * 3);
  const pGeo = new THREE.BufferGeometry();
  pGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const dot = softDotTexture();
  const pMat = new THREE.PointsMaterial({
    size: 2.2, map: dot, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, sizeAttenuation: true,
    color: STATE_TARGETS.idle.color.clone(), opacity: STATE_TARGETS.idle.brightness,
  });
  const points = new THREE.Points(pGeo, pMat);
  group.add(points);

  // ── Lines ──
  const pairs = buildPairs(dirs);
  const pairCount = pairs.length / 2;
  const linePos = new Float32Array(pairCount * 2 * 3);
  const lGeo = new THREE.BufferGeometry();
  lGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  const lMat = new THREE.LineBasicMaterial({
    color: STATE_TARGETS.idle.color.clone(), transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, opacity: STATE_TARGETS.idle.lineOpacity,
  });
  const lines = new THREE.LineSegments(lGeo, lMat);
  group.add(lines);

  // ── Electrons (bright travellers along lines, mainly in "thinking") ──
  const electrons = Array.from({ length: ELECTRON_POOL }, () => ({
    pair: 0, t: 0, speed: 0, active: false,
  }));
  const ePos = new Float32Array(ELECTRON_POOL * 3);
  const eGeo = new THREE.BufferGeometry();
  eGeo.setAttribute("position", new THREE.BufferAttribute(ePos, 3));
  const eMat = new THREE.PointsMaterial({
    size: 3.4, map: dot, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, sizeAttenuation: true,
    color: new THREE.Color(0xffffff), opacity: 0,
  });
  const ePoints = new THREE.Points(eGeo, eMat);
  group.add(ePoints);

  // ── Live/interpolated values ──
  let target = STATE_TARGETS.idle;
  const cur = {
    radius: target.radius, speed: target.speed, motion: target.motion,
    brightness: target.brightness, lineDensity: target.lineDensity,
    lineOpacity: target.lineOpacity, electron: target.electron,
    color: target.color.clone(),
  };
  let tumble = 0; // decaying angular impulse on state change

  // ── Audio ──
  let analyser: AnalyserNode | null = null;
  let freq: Uint8Array | null = null;
  let bass = 0, mid = 0;

  function readAudio() {
    if (!analyser || !freq) { bass *= 0.9; mid *= 0.9; return; }
    analyser.getByteFrequencyData(freq as any);
    const n = freq.length;
    let lo = 0, md = 0;
    const loEnd = Math.max(2, Math.floor(n * 0.12));
    const mdEnd = Math.floor(n * 0.5);
    for (let i = 0; i < loEnd; i++) lo += freq[i];
    for (let i = loEnd; i < mdEnd; i++) md += freq[i];
    lo = lo / (loEnd * 255);
    md = md / ((mdEnd - loEnd) * 255);
    bass += (lo - bass) * 0.35;
    mid += (md - mid) * 0.25;
  }

  // ── Animation loop ──
  const clock = new THREE.Clock();
  let raf = 0;
  let running = true;

  function frame() {
    if (!running) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    readAudio();

    // Ease interpolated values toward the current state's targets.
    const k = 1 - Math.pow(0.001, dt); // frame-rate independent smoothing
    cur.radius += (target.radius - cur.radius) * k;
    cur.speed += (target.speed - cur.speed) * k;
    cur.motion += (target.motion - cur.motion) * k;
    cur.brightness += (target.brightness - cur.brightness) * k;
    cur.lineDensity += (target.lineDensity - cur.lineDensity) * k;
    cur.lineOpacity += (target.lineOpacity - cur.lineOpacity) * k;
    cur.electron += (target.electron - cur.electron) * k;
    cur.color.lerp(target.color, k);

    const bassPush = bass * 6.5;        // bass pushes particles outward
    const radius = cur.radius + bassPush;
    const floatAmp = 1.1 + bass * 1.5;

    // Update particle positions.
    for (let i = 0; i < COUNT; i++) {
      const off = Math.sin(t * cur.motion + phases[i]) * floatAmp;
      const r = radius + off;
      positions[i * 3] = dirs[i * 3] * r;
      positions[i * 3 + 1] = dirs[i * 3 + 1] * r;
      positions[i * 3 + 2] = dirs[i * 3 + 2] * r;
    }
    pGeo.attributes.position.needsUpdate = true;
    pMat.opacity = Math.min(1, cur.brightness + bass * 0.4);
    pMat.size = 2.2 + bass * 1.8 + mid * 0.6;
    pMat.color.copy(cur.color);

    // Update lines (only the densest `lineDensity` fraction).
    const active = Math.floor(pairCount * cur.lineDensity);
    for (let p = 0; p < active; p++) {
      const a = pairs[p * 2], b = pairs[p * 2 + 1];
      const o = p * 6;
      linePos[o] = positions[a * 3];
      linePos[o + 1] = positions[a * 3 + 1];
      linePos[o + 2] = positions[a * 3 + 2];
      linePos[o + 3] = positions[b * 3];
      linePos[o + 4] = positions[b * 3 + 1];
      linePos[o + 5] = positions[b * 3 + 2];
    }
    lGeo.setDrawRange(0, active * 2);
    lGeo.attributes.position.needsUpdate = true;
    lMat.opacity = cur.lineOpacity + bass * 0.15;
    lMat.color.copy(cur.color);

    // Electrons — spawn/advance only when electron intensity is meaningful.
    let eActive = 0;
    const spawnChance = cur.electron * 0.5;
    for (const e of electrons) {
      if (!e.active) {
        if (Math.random() < spawnChance * dt * 60 && active > 0) {
          e.active = true;
          e.pair = Math.floor(Math.random() * active);
          e.t = 0;
          e.speed = 0.6 + Math.random() * 1.2;
        }
      }
      if (e.active) {
        e.t += e.speed * dt;
        if (e.t >= 1 || e.pair >= active) {
          e.active = false;
        } else {
          const a = pairs[e.pair * 2], b = pairs[e.pair * 2 + 1];
          const it = e.t;
          ePos[eActive * 3] = positions[a * 3] + (positions[b * 3] - positions[a * 3]) * it;
          ePos[eActive * 3 + 1] = positions[a * 3 + 1] + (positions[b * 3 + 1] - positions[a * 3 + 1]) * it;
          ePos[eActive * 3 + 2] = positions[a * 3 + 2] + (positions[b * 3 + 2] - positions[a * 3 + 2]) * it;
          eActive++;
        }
      }
    }
    eGeo.setDrawRange(0, eActive);
    eGeo.attributes.position.needsUpdate = true;
    eMat.opacity = Math.min(1, cur.electron);

    // Rotation: steady orbit + decaying tumble on state change.
    tumble *= Math.pow(0.02, dt);
    group.rotation.y += (cur.speed + tumble) * dt;
    group.rotation.x += tumble * 0.6 * dt;

    // Camera depth breathing.
    camera.position.z = 78 + Math.sin(t * 0.4) * 3 - bass * 4;
    camera.lookAt(0, 0, 0);

    renderer.render(scene, camera);
    raf = requestAnimationFrame(frame);
  }

  function resize() {
    const w = canvas.clientWidth || canvas.width;
    const h = canvas.clientHeight || canvas.height;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();

  // Boot-up: start in thinking, settle to idle shortly after (the spec's boot anim).
  target = STATE_TARGETS.thinking;
  setTimeout(() => { if (running) { target = STATE_TARGETS.idle; tumble = 2.5; } }, 1400);

  raf = requestAnimationFrame(frame);

  return {
    setState(s: OrbState) {
      const next = STATE_TARGETS[s];
      if (next !== target) tumble += 2.2; // transition tumble
      target = next;
    },
    setAnalyser(a: AnalyserNode | null) {
      analyser = a;
      freq = a ? new Uint8Array(a.frequencyBinCount) : null;
      if (!a) { bass = 0; mid = 0; }
    },
    destroy() {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      pGeo.dispose(); lGeo.dispose(); eGeo.dispose();
      pMat.dispose(); lMat.dispose(); eMat.dispose();
      dot.dispose();
      renderer.dispose();
    },
  };
}
