import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

// Crazyflie world frame: X forward, Y left, Z up (meters)
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const $ = (id) => document.getElementById(id);
const MODEL = new URLSearchParams(location.search).get('model') || 'crazyflie';
const GIZMO = 96;
const TRAIL_MAX = 60000;

/* ------------------------------------------------------------------ editor */

const editor = CodeMirror($('editor'), {
  mode: 'python', lineNumbers: true, indentUnit: 4, tabSize: 4, indentWithTabs: false,
  extraKeys: { Tab: (cm) => cm.replaceSelection('    '), 'Cmd-S': save, 'Ctrl-S': save },
});
let markedLines = [];

function markLine(line, cls) {
  const h = editor.addLineClass(line - 1, 'background', cls);
  markedLines.push([h, cls]);
}
function clearMarks(cls) {
  markedLines = markedLines.filter(([h, c]) => {
    if (cls && c !== cls) return true;
    editor.removeLineClass(h, 'background', c);
    return false;
  });
}

/* ------------------------------------------------------------------- scene */

const host = $('viewport');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setClearColor(0xffffff, 1);
renderer.autoClear = false;
host.appendChild(renderer.domElement);

const labelRenderer = new CSS2DRenderer();
Object.assign(labelRenderer.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' });
host.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 200);
camera.position.set(-2.5, -4, 2.6);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(1, 0, 0.4);
controls.enableDamping = true;
controls.dampingFactor = 0.12;
let userMoved = false;
controls.addEventListener('start', () => { userMoved = true; });

scene.add(new THREE.HemisphereLight(0xffffff, 0xd9d9d9, 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 2.0);
sun.position.set(3, -2, 6);
scene.add(sun);

function label(text, cls, parent, pos) {
  const el = document.createElement('div');
  el.className = `lbl ${cls || ''}`;
  el.textContent = text;
  const obj = new CSS2DObject(el);
  if (pos) obj.position.set(...pos);
  parent.add(obj);
  return obj;
}

function disposeGroup(g) {
  g.traverse((o) => {
    o.geometry?.dispose();
    if (o.material) [].concat(o.material).forEach((m) => m.dispose());
    if (o.isCSS2DObject) o.element.remove();
  });
  g.clear();
}

/* ---------------------------------------------------------- grid + origin */

const gridGroup = new THREE.Group();
scene.add(gridGroup);
let grid = null;

function buildGrid(b) {
  disposeGroup(gridGroup);
  const minor = [], major = [];
  for (let x = b.x0; x <= b.x1 + 1e-9; x += 0.5) (Number.isInteger(x) ? major : minor).push(x, b.y0, 0, x, b.y1, 0);
  for (let y = b.y0; y <= b.y1 + 1e-9; y += 0.5) (Number.isInteger(y) ? major : minor).push(b.x0, y, 0, b.x1, y, 0);
  for (const [pts, color] of [[minor, 0xf0f0f2], [major, 0xd6d6db]]) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color }));
    lines.position.z = -0.002;
    gridGroup.add(lines);
  }
  for (let x = b.x0; x <= b.x1; x++) if (x) label(`${x}`, 'tick', gridGroup, [x, b.y0 - 0.15, 0]);
  for (let y = b.y0; y <= b.y1; y++) if (y) label(`${y}`, 'tick', gridGroup, [b.x0 - 0.15, y, 0]);
  grid = b;
}

function fitGrid(points) {
  const b = { x0: -1, x1: 1, y0: -1, y1: 1 };
  for (const p of points) {
    b.x0 = Math.min(b.x0, Math.floor(p[0] - 0.5)); b.x1 = Math.max(b.x1, Math.ceil(p[0] + 0.5));
    b.y0 = Math.min(b.y0, Math.floor(p[1] - 0.5)); b.y1 = Math.max(b.y1, Math.ceil(p[1] + 0.5));
  }
  if (grid && b.x0 >= grid.x0 && b.x1 <= grid.x1 && b.y0 >= grid.y0 && b.y1 <= grid.y1) return;
  if (grid) Object.assign(b, { x0: Math.min(b.x0, grid.x0), x1: Math.max(b.x1, grid.x1),
                               y0: Math.min(b.y0, grid.y0), y1: Math.max(b.y1, grid.y1) });
  buildGrid(b);
}
buildGrid({ x0: -1, x1: 3, y0: -2, y1: 2 });

const originGroup = new THREE.Group();
[[1, 0, 0, 0xe0312b, 'x'], [0, 1, 0, 0x1a9f5b, 'y'], [0, 0, 1, 0x2f6bff, 'z']].forEach(([x, y, z, c, n]) => {
  originGroup.add(new THREE.ArrowHelper(new THREE.Vector3(x, y, z), new THREE.Vector3(), 0.5, c, 0.07, 0.035));
  label(n.toUpperCase(), `axis ${n}`, originGroup, [x * 0.6, y * 0.6, z * 0.6]);
});
originGroup.add(new THREE.Mesh(new THREE.SphereGeometry(0.018, 16, 12), new THREE.MeshBasicMaterial({ color: 0x000000 })));
scene.add(originGroup);

/* ------------------------------------------------------------------- drone */

const drone = new THREE.Group();
const droneModel = new THREE.Group();
drone.add(droneModel);
drone.visible = false;
scene.add(drone);

const ghost = new THREE.Group();
const ghostModel = new THREE.Group();
ghost.add(ghostModel);
ghost.visible = false;
scene.add(ghost);

// Body axes (red = forward) so heading is readable regardless of the mesh
droneModel.add(new THREE.AxesHelper(0.06));

// Drop line + floor ring under the drone for depth perception
const dropGeom = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
const dropLine = new THREE.Line(dropGeom, new THREE.LineDashedMaterial({ color: 0x9a9aa0, dashSize: 0.03, gapSize: 0.02 }));
const floorRing = new THREE.Mesh(new THREE.RingGeometry(0.035, 0.045, 32), new THREE.MeshBasicMaterial({ color: 0x000000 }));
dropLine.visible = floorRing.visible = false;
scene.add(dropLine, floorRing);

async function loadURDF(name) {
  const xml = await (await fetch(`urdf/${name}.urdf.xacro`)).text();
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  const loader = new ColladaLoader();
  const parts = [];
  for (const visual of doc.querySelectorAll('link > visual')) {
    const mesh = visual.querySelector('geometry > mesh');
    if (!mesh) continue;
    const url = mesh.getAttribute('filename').replace(/^package:\/\/[^/]+\//, 'urdf/');
    const collada = await loader.loadAsync(url);
    const obj = collada.scene;
    obj.rotation.set(0, 0, 0); // URDF is already Z-up; undo the loader's Y-up conversion
    const wrap = new THREE.Group();
    const origin = visual.querySelector('origin');
    if (origin) {
      const xyz = (origin.getAttribute('xyz') || '0 0 0').trim().split(/\s+/).map(Number);
      const rpy = (origin.getAttribute('rpy') || '0 0 0').trim().split(/\s+/).map(Number);
      wrap.position.set(...xyz);
      wrap.rotation.copy(new THREE.Euler(rpy[0], rpy[1], rpy[2], 'ZYX'));
    }
    const s = mesh.getAttribute('scale');
    if (s) wrap.scale.set(...s.trim().split(/\s+/).map(Number));
    wrap.add(obj);
    parts.push(wrap);
  }
  return parts;
}

function fallbackModel() {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0x222222 });
  for (const a of [Math.PI / 4, -Math.PI / 4]) {
    const arm = new THREE.Mesh(new THREE.BoxGeometry(0.092, 0.006, 0.004), mat);
    arm.rotation.z = a;
    g.add(arm);
  }
  for (const [x, y] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    const p = new THREE.Mesh(new THREE.CylinderGeometry(0.023, 0.023, 0.002, 24),
      new THREE.MeshStandardMaterial({ color: 0x999999, transparent: true, opacity: 0.6 }));
    p.rotation.x = Math.PI / 2;
    p.position.set(x * 0.032, y * 0.032, 0.012);
    g.add(p);
  }
  return [g];
}

(async () => {
  let parts;
  try { parts = await loadURDF(MODEL); } catch (e) { console.warn('URDF load failed', e); parts = fallbackModel(); }
  const ghostMat = new THREE.MeshBasicMaterial({ color: 0x0a66ff, transparent: true, opacity: 0.28, depthWrite: false });
  for (const p of parts) {
    droneModel.add(p);
    const g = p.clone(true);
    g.traverse((o) => { if (o.isMesh) o.material = ghostMat; });
    ghostModel.add(g);
  }
  setScale(Number($('scale').value));
})();

function setScale(s) {
  droneModel.scale.setScalar(s);
  ghostModel.scale.setScalar(s);
  $('scaleval').textContent = `${s}×`;
}

/* ------------------------------------------------------------------- trail */

// Thick trail: WebGL lines are always 1 px, so use Line2 in chunks. Only the newest chunk
// is rebuilt as points arrive; full chunks are left alone.
const TRAIL_CHUNK = 400;
const trailGroup = new THREE.Group();
scene.add(trailGroup);
const trailMat = new LineMaterial({ vertexColors: true, linewidth: 3.5, worldUnits: false });
let chunks = [];
let chunk = null;
let trailLast = null;
let trailN = 0;
let vmax = 1;

const STOPS = [[0, 0x2f6bff], [0.35, 0x19b6a3], [0.7, 0xf0b400], [1, 0xff5a36]].map(([t, c]) => [t, new THREE.Color(c)]);
const tmpColor = new THREE.Color();
function speedColor(v) {
  const t = Math.min(1, Math.max(0, v / vmax));
  for (let i = 1; i < STOPS.length; i++) {
    if (t <= STOPS[i][0]) {
      const [t0, c0] = STOPS[i - 1], [t1, c1] = STOPS[i];
      return tmpColor.copy(c0).lerp(c1, (t - t0) / (t1 - t0));
    }
  }
  return tmpColor.copy(STOPS[STOPS.length - 1][1]);
}

function buildChunk(c) {
  if (c.pos.length < 6) return;
  const g = new LineGeometry();
  g.setPositions(c.pos);
  g.setColors(c.col);
  c.line.geometry.dispose();
  c.line.geometry = g;
}

function newChunk() {
  if (chunk) buildChunk(chunk);
  const line = new Line2(new LineGeometry(), trailMat);
  line.frustumCulled = false;
  trailGroup.add(line);
  chunk = { line, pos: [], col: [] };
  if (trailLast) { chunk.pos.push(...trailLast.p); chunk.col.push(...trailLast.c); } // stay connected
  chunks.push(chunk);
  while (trailN > TRAIL_MAX && chunks.length > 1) {
    const old = chunks.shift();
    trailN -= old.pos.length / 3;
    old.line.geometry.dispose();
    trailGroup.remove(old.line);
  }
}

function addTrail(p, speed, rebuild = true) {
  if (trailLast && Math.hypot(p[0] - trailLast.p[0], p[1] - trailLast.p[1], p[2] - trailLast.p[2]) < 0.003) return;
  if (!chunk || chunk.pos.length / 3 >= TRAIL_CHUNK) newChunk();
  const c = speedColor(speed);
  chunk.pos.push(p[0], p[1], p[2]);
  chunk.col.push(c.r, c.g, c.b);
  trailLast = { p: [p[0], p[1], p[2]], c: [c.r, c.g, c.b] };
  trailN++;
  if (rebuild) buildChunk(chunk);
}

function flushTrail() {
  if (chunk) buildChunk(chunk);
}

function clearTrail() {
  for (const c of chunks) { c.line.geometry.dispose(); trailGroup.remove(c.line); }
  chunks = [];
  chunk = null;
  trailLast = null;
  trailN = 0;
}

/* -------------------------------------------------------------------- plan */

const planGroup = new THREE.Group();
scene.add(planGroup);
let plan = null;      // from /api/plan
let sim = null;       // simulated setpoint timeline
let planAnchor = null;
let wpLabels = [];

const smooth7 = (u) => u * u * u * u * (35 + u * (-84 + u * (70 - 20 * u)));

// Mirrors the firmware high-level planner: each command starts from the current
// setpoint at the moment it is sent and follows a 7th-order rest-to-rest polynomial.
function simulate(ops, start) {
  const segs = [];
  const at = (t) => {
    let seg = null;
    for (const s of segs) { if (s.t0 <= t) seg = s; else break; }
    if (!seg) return { p: start.slice(), yaw: 0, seg: null };
    const u = seg.dur > 0 ? Math.min(1, Math.max(0, (t - seg.t0) / seg.dur)) : 1;
    const k = seg.linear ? u : smooth7(u);
    return { p: seg.from.map((a, i) => a + (seg.to[i] - a) * k), yaw: seg.yaw0 + (seg.yaw1 - seg.yaw0) * k, seg };
  };
  let end = 0, stopT = null, peak = 0;
  for (const op of ops) {
    if (op.op === 'stop') { stopT ??= op.t; continue; }
    if (!['takeoff', 'land', 'go_to'].includes(op.op)) continue;
    const cur = at(op.t);
    let to, yaw1 = cur.yaw;
    if (op.op === 'go_to') {
      to = op.relative ? [cur.p[0] + op.x, cur.p[1] + op.y, cur.p[2] + op.z] : [op.x, op.y, op.z];
      yaw1 = op.relative ? cur.yaw + op.yaw : op.yaw;
    } else {
      to = [cur.p[0], cur.p[1], op.z];
    }
    const dist = Math.hypot(to[0] - cur.p[0], to[1] - cur.p[1], to[2] - cur.p[2]);
    if (op.duration > 0) peak = Math.max(peak, dist / op.duration * (op.linear ? 1 : 35 / 16));
    segs.push({ op, from: cur.p, to, t0: op.t, dur: op.duration, yaw0: cur.yaw, yaw1, linear: op.linear });
    end = Math.max(end, op.t + op.duration);
  }
  const duration = Math.max(end, plan?.duration || 0);
  const samples = [];
  for (let t = 0; t <= (stopT ?? duration) + 1e-9; t += 0.02) samples.push(at(t).p);
  return { segs, at, duration, stopT, peak, samples };
}

function rebuildPlan() {
  disposeGroup(planGroup);
  wpLabels = [];
  if (!plan) return;
  planAnchor = anchorPoint();
  sim = simulate(plan.ops, planAnchor);
  vmax = Math.max(0.5, Math.ceil(sim.peak * 2) / 2);
  $('vmax').textContent = `${vmax} m/s`;

  if (sim.samples.length > 1) {
    const g = new THREE.BufferGeometry().setFromPoints(sim.samples.map((p) => new THREE.Vector3(...p)));
    const line = new THREE.Line(g, new THREE.LineDashedMaterial({ color: 0x000000, dashSize: 0.04, gapSize: 0.03 }));
    line.computeLineDistances();
    planGroup.add(line);
  }
  const dotGeom = new THREE.SphereGeometry(0.018, 16, 12);
  const dotMat = new THREE.MeshBasicMaterial({ color: 0x000000 });
  const startDot = new THREE.Mesh(dotGeom, new THREE.MeshBasicMaterial({ color: 0x0a66ff }));
  startDot.position.set(...planAnchor);
  planGroup.add(startDot);
  // One label per distinct target, showing the exact coordinates from the code
  const seen = new Map();
  for (const s of sim.segs) {
    const key = s.to.map((v) => v.toFixed(2)).join(', ');
    const dot = new THREE.Mesh(dotGeom, dotMat);
    dot.position.set(...s.to);
    planGroup.add(dot);
    if (!seen.has(key)) {
      const l = label(key, 'wp', planGroup, [s.to[0], s.to[1], s.to[2] + 0.07]);
      seen.set(key, l);
      wpLabels.push(l);
    }
    s.label = seen.get(key);
  }
  applyLayers();
  fitGrid([...sim.samples, ...bsPositions()]);
}

function anchorPoint() {
  // takeoff() starts from wherever the drone is; before any telemetry assume the origin
  if (runState === 'fly' && planAnchor) return planAnchor;
  return lastPose ? [lastPose.p[0], lastPose.p[1], Math.max(0, lastPose.p[2])] : [0, 0, 0];
}

let planTimer = null;
function schedulePlan() {
  clearTimeout(planTimer);
  planTimer = setTimeout(fetchPlan, 250);
}

async function fetchPlan() {
  const res = await fetch('/api/plan', { method: 'POST', body: JSON.stringify({ code: editor.getValue() }) });
  plan = await res.json();
  clearMarks('cm-line-warn');
  clearMarks('cm-line-err');
  for (const w of plan.warnings) {
    const m = w.match(/:(\d+):/);
    if (m) markLine(Number(m[1]), plan.error_line ? 'cm-line-err' : 'cm-line-warn');
  }
  showUri(plan.uri);
  rebuildPlan();
}

/* ------------------------------------------- plan clock (simulate / live) */

let clock = null; // { t0: ms, kind: 'sim'|'live', printIdx }

function planTime() {
  return clock?.t0 != null ? (performance.now() - clock.t0) / 1000 : null;
}

function startSim() {
  if (!sim || runState === 'fly') return;
  clock = { t0: performance.now(), kind: 'sim' };
  $('bSim').classList.add('active');
  updateDroneVisibility();
}

function stopClock() {
  clock = null;
  ghost.visible = false;
  clearMarks('cm-line-active');
  wpLabels.forEach((l) => l.element.classList.remove('active'));
  $('bSim').classList.remove('active');
  updateDroneVisibility();
}

// Simulation shows only the simulated drone; a hardware run shows only the real one
function updateDroneVisibility() {
  drone.visible = runState === 'fly' && lastPose != null && clock?.kind !== 'sim';
}

// During a real run, re-sync the plan clock whenever the script prints a line the plan knows about
function syncOnPrint(text) {
  if (!clock || clock.kind !== 'live' || !plan) return;
  const prints = plan.ops.filter((o) => o.op === 'print');
  for (let i = clock.printIdx; i < prints.length; i++) {
    if (prints[i].text === text) {
      clock.t0 = performance.now() - prints[i].t * 1000;
      clock.printIdx = i + 1;
      return;
    }
  }
}

let activeLine = null;
function updateClock() {
  const t = planTime();
  if (t == null) return;
  if (clock.kind === 'sim' && t > sim.duration + 1) { stopClock(); return; }
  const s = sim.at(Math.min(t, sim.stopT ?? Infinity));
  if (clock.kind === 'sim') {
    ghost.visible = $('layerPlan').checked;
    ghost.position.set(...s.p);
    ghost.rotation.set(0, 0, s.yaw);
    const ahead = sim.at(Math.min(t + 0.05, sim.stopT ?? Infinity)).p;
    showReadout(s.p, Math.hypot(ahead[0] - s.p[0], ahead[1] - s.p[1], ahead[2] - s.p[2]) / 0.05);
    attitude = { roll: 0, pitch: 0, yaw: s.yaw };
  }

  let op = null;
  for (const o of plan.ops) { if (o.t <= t) op = o; else break; }
  const line = op?.line ?? null;
  if (line !== activeLine) {
    clearMarks('cm-line-active');
    if (line) { markLine(line, 'cm-line-active'); editor.scrollIntoView({ line: line - 1, ch: 0 }, 80); }
    activeLine = line;
  }
  wpLabels.forEach((l) => l.element.classList.toggle('active', l === s.seg?.label));
}

/* ----------------------------------------------------------- base stations */

const bsGroup = new THREE.Group();
scene.add(bsGroup);
let geos = {};
const bsObjs = {};

function bsPositions() {
  return Object.values(geos).map((g) => g.origin);
}

function buildBaseStations() {
  disposeGroup(bsGroup);
  for (const k of Object.keys(bsObjs)) delete bsObjs[k];
  const bodyMat = new THREE.MeshStandardMaterial({ color: 0x1c1c1e, roughness: 0.5 });
  const lensMat = new THREE.MeshStandardMaterial({ color: 0x5a5a60, roughness: 0.2, metalness: 0.3 });
  const fovMat = new THREE.MeshBasicMaterial({ color: 0x0a66ff, transparent: true, opacity: 0.025, side: THREE.DoubleSide, depthWrite: false });
  const fovEdge = new THREE.LineBasicMaterial({ color: 0x0a66ff, transparent: true, opacity: 0.25 });

  for (const [id, g] of Object.entries(geos)) {
    const o = g.origin, R = g.rot;
    const frame = new THREE.Group();
    frame.matrixAutoUpdate = false;
    frame.matrix.set(R[0][0], R[0][1], R[0][2], o[0],
                     R[1][0], R[1][1], R[1][2], o[1],
                     R[2][0], R[2][1], R[2][2], o[2],
                     0, 0, 0, 1);
    // Lighthouse 2 body; it looks along its local +X axis
    frame.add(new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.075, 0.075), bodyMat));
    const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.026, 0.026, 0.006, 32), lensMat);
    lens.rotation.z = Math.PI / 2;
    lens.position.x = 0.032;
    frame.add(lens);
    frame.add(new THREE.AxesHelper(0.14));

    // Field of view: 150° horizontal x 110° vertical
    const d = 0.25, hw = d * Math.tan(THREE.MathUtils.degToRad(75)), hh = d * Math.tan(THREE.MathUtils.degToRad(55));
    const c = [[d, hw, hh], [d, -hw, hh], [d, -hw, -hh], [d, hw, -hh]];
    const fovGeom = new THREE.BufferGeometry();
    fovGeom.setAttribute('position', new THREE.Float32BufferAttribute(
      [0, 0, 0, ...c[0], ...c[1], 0, 0, 0, ...c[1], ...c[2], 0, 0, 0, ...c[2], ...c[3], 0, 0, 0, ...c[3], ...c[0]], 3));
    const fov = new THREE.Group();
    fov.add(new THREE.Mesh(fovGeom, fovMat));
    const edges = [];
    for (let i = 0; i < 4; i++) edges.push(0, 0, 0, ...c[i], ...c[i], ...c[(i + 1) % 4]);
    const eg = new THREE.BufferGeometry();
    eg.setAttribute('position', new THREE.Float32BufferAttribute(edges, 3));
    fov.add(new THREE.LineSegments(eg, fovEdge));
    frame.add(fov);
    bsGroup.add(frame);

    const n = Number(id) + 1;
    const lbl = label(`${n}`, 'bs', bsGroup, [o[0], o[1], o[2] + 0.11]);

    // Height drop to the floor
    const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...o), new THREE.Vector3(o[0], o[1], 0)]),
      new THREE.LineDashedMaterial({ color: 0xb0b0b5, dashSize: 0.05, gapSize: 0.04 }));
    drop.computeLineDistances();
    bsGroup.add(drop);
    const foot = new THREE.Mesh(new THREE.RingGeometry(0.03, 0.04, 24), new THREE.MeshBasicMaterial({ color: 0x1c1c1e }));
    foot.position.set(o[0], o[1], 0);
    bsGroup.add(foot);
    label(`${o[2].toFixed(2)} m`, 'h', bsGroup, [o[0], o[1], o[2] / 2]);

    const ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...o), new THREE.Vector3(...o)]),
      new THREE.LineBasicMaterial({ color: 0x1a9f5b, transparent: true, opacity: 0.55 }));
    ray.visible = false;
    bsGroup.add(ray);
    bsObjs[id] = { fov, lbl, ray, origin: o };
  }
  applyLayers();
  if (sim) fitGrid([...sim.samples, ...bsPositions()]);
  if (!userMoved) setView('iso');
  renderBsChips();
}

/* ---------------------------------------------------------------- gizmo */

const gScene = new THREE.Scene();
const gCam = new THREE.OrthographicCamera(-1.7, 1.7, 1.7, -1.7, 0.1, 10);
const gTargets = [];

function gizmoSprite(text, color) {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const ctx = c.getContext('2d');
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(32, 32, 30, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#fff';
  ctx.font = '700 34px Roboto, sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, 32, 34);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c) }));
  s.scale.setScalar(0.62);
  return s;
}

[[new THREE.Vector3(1, 0, 0), '#e0312b', 'X'], [new THREE.Vector3(0, 1, 0), '#1a9f5b', 'Y'], [new THREE.Vector3(0, 0, 1), '#2f6bff', 'Z']]
  .forEach(([dir, color, name]) => {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), dir.clone()]),
      new THREE.LineBasicMaterial({ color }));
    gScene.add(line);
    const pos = gizmoSprite(name, color);
    pos.position.copy(dir).multiplyScalar(1.05);
    pos.userData.dir = dir.clone();
    gScene.add(pos);
    const neg = gizmoSprite('', color);
    neg.material.opacity = 0.35;
    neg.material.transparent = true;
    neg.scale.setScalar(0.4);
    neg.position.copy(dir).multiplyScalar(-1.05);
    neg.userData.dir = dir.clone().negate();
    gScene.add(neg);
    gTargets.push(pos, neg);
  });

renderer.domElement.addEventListener('pointerdown', (e) => {
  const r = renderer.domElement.getBoundingClientRect();
  const x = e.clientX - r.left, y = r.bottom - e.clientY;
  if (x > GIZMO || y > GIZMO) return;
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2((x / GIZMO) * 2 - 1, (y / GIZMO) * 2 - 1), gCam);
  const hit = ray.intersectObjects(gTargets)[0];
  if (!hit) return;
  e.stopImmediatePropagation();
  const dir = hit.object.userData.dir;
  const dist = camera.position.distanceTo(controls.target);
  // Looking straight down Z confuses OrbitControls, so nudge the top view slightly
  const d = Math.abs(dir.z) === 1 ? new THREE.Vector3(0, -0.001, dir.z) : dir;
  flyCamera(controls.target.clone(), controls.target.clone().addScaledVector(d.clone().normalize(), dist));
}, { capture: true });

/* --------------------------------------------------------------- attitude */

const att = $('attitude').getContext('2d');
let attitude = { roll: 0, pitch: 0, yaw: 0 };

function drawAttitude() {
  const W = 220, cx = 110, cy = 105, r = 95;
  const { roll, pitch, yaw } = attitude;
  att.clearRect(0, 0, W, 250);
  att.save();
  att.beginPath(); att.arc(cx, cy, r, 0, Math.PI * 2); att.clip();
  att.translate(cx, cy);
  att.rotate(-roll);
  const k = 2.2; // px per degree
  const off = THREE.MathUtils.radToDeg(pitch) * k;
  att.fillStyle = '#e7f0ff'; att.fillRect(-2 * r, -2 * r + off, 4 * r, 2 * r);
  att.fillStyle = '#f1ebe0'; att.fillRect(-2 * r, off, 4 * r, 2 * r);
  att.strokeStyle = '#000'; att.lineWidth = 2;
  att.beginPath(); att.moveTo(-2 * r, off); att.lineTo(2 * r, off); att.stroke();
  att.lineWidth = 1; att.strokeStyle = 'rgba(0,0,0,.35)';
  for (const deg of [-20, -10, 10, 20]) {
    const y = off - deg * k, w = deg % 20 ? 14 : 24;
    att.beginPath(); att.moveTo(-w, y); att.lineTo(w, y); att.stroke();
  }
  att.restore();
  att.strokeStyle = '#e6e6e9'; att.lineWidth = 2;
  att.beginPath(); att.arc(cx, cy, r, 0, Math.PI * 2); att.stroke();
  att.strokeStyle = '#0a66ff'; att.lineWidth = 3.5;
  att.beginPath(); att.moveTo(cx - 42, cy); att.lineTo(cx - 14, cy); att.lineTo(cx, cy + 10); att.lineTo(cx + 14, cy); att.lineTo(cx + 42, cy); att.stroke();
  att.fillStyle = '#000';
  att.font = '500 21px "Roboto Mono", monospace';
  att.textAlign = 'center';
  att.fillText(`${Math.round(THREE.MathUtils.radToDeg(yaw))}°`, cx, 238);
}
drawAttitude();

/* ------------------------------------------------------------ live state */

let lastPose = null;
let connected = false;
let runState = 'idle';
let lh = null;
const euler = new THREE.Euler();
const quat = new THREE.Quaternion();

function showReadout(p, speed) {
  $('px').textContent = p[0].toFixed(3);
  $('py').textContent = p[1].toFixed(3);
  $('pz').textContent = p[2].toFixed(3);
  $('spd').textContent = speed.toFixed(2);
}

let lastLiveLog = 0;
function handlePose(ev) {
  const first = !lastPose;
  lastPose = ev;
  const [x, y, z] = ev.p;
  drone.position.set(x, y, z);
  quat.set(...ev.q);
  drone.quaternion.copy(quat);
  updateDroneVisibility();
  const speed = Math.hypot(...ev.v);
  if (clock?.kind === 'sim') return; // the simulation owns the readouts while it plays
  if (runState === 'fly') addTrail(ev.p, speed);
  showReadout(ev.p, speed);
  euler.setFromQuaternion(quat, 'ZYX');
  attitude = { roll: euler.x, pitch: -euler.y, yaw: euler.z };
  // Live data in the output panel while flying, twice a second
  const now = performance.now();
  if (runState === 'fly' && now - lastLiveLog > 500) {
    lastLiveLog = now;
    const d = (a) => THREE.MathUtils.radToDeg(a).toFixed(1).padStart(6);
    const f = (v) => v.toFixed(3).padStart(7);
    const rx = [...Array(16).keys()].filter((i) => (lh?.receive >> i) & 1).map((i) => i + 1).join(',') || '-';
    log(`x${f(x)} y${f(y)} z${f(z)}  |v|${speed.toFixed(2).padStart(5)}  roll${d(attitude.roll)} pitch${d(attitude.pitch)} yaw${d(attitude.yaw)}` +
      `  ${lh?.vbat != null ? lh.vbat.toFixed(2) + ' V' : ''}  LH ${rx}`, 'live');
  }
  if (first || (runState !== 'fly' && planAnchor && Math.hypot(x - planAnchor[0], y - planAnchor[1]) > 0.05)) rebuildPlan();
}

function renderBsChips() {
  const ids = new Set(Object.keys(geos).map(Number));
  for (const mask of [lh?.geoVal, lh?.active, lh?.receive]) {
    for (let i = 0; i < 16; i++) if (mask && (mask >> i) & 1) ids.add(i);
  }
  const box = $('lhbs');
  box.innerHTML = '';
  if (!ids.size) { box.textContent = '—'; return; }
  for (const i of [...ids].sort((a, b) => a - b)) {
    const el = document.createElement('i');
    el.textContent = i + 1;
    const geo = geos[i] || (lh?.geoVal >> i) & 1;
    const rx = (lh?.receive >> i) & 1;
    if (geo) el.classList.add('geo');
    if (rx) el.classList.add('rx');
    el.title = `BS${i + 1}: ${geo ? 'geometry' : 'no geometry'}, ${(lh?.calVal >> i) & 1 ? 'calibrated' : 'no calibration'}, ${rx ? 'receiving' : 'not receiving'}`;
    box.appendChild(el);
  }
}

function handleLh(ev) {
  lh = ev;
  renderBsChips();
  const st = ['not receiving', 'missing data', 'to estimator'][ev.status] ?? '—';
  const dot = $('lhdot');
  dot.className = `dot ${ev.status === 2 ? 'ok' : ev.status === 1 ? 'warn' : ''}`;
  $('cLh').title = `Lighthouse: ${st}`;
  for (const [id, o] of Object.entries(bsObjs)) o.lbl.element.classList.toggle('rx', !!((ev.receive >> id) & 1));

}

function handleMeta(meta) {
  if ('connected' in meta) connected = meta.connected;
  if ('run' in meta) {
    const prev = runState;
    runState = meta.run;
    if (runState === 'fly' && prev !== 'fly') {
      stopClock();
      planAnchor = anchorPoint();
      clock = { t0: null, kind: 'live', printIdx: 0 }; // highlights the running line; no simulated drone
    }
    if (runState === 'idle' && clock?.kind === 'live') stopClock();
    updateDroneVisibility();
  }
  if (meta.status && meta.status !== lastStatus) {
    lastStatus = meta.status;
    log(meta.status, meta.level === 'error' ? 'err' : meta.level === 'warn' ? 'warn' : 'sys');
  }
  const dot = $('cConn').querySelector('.dot');
  dot.className = `dot ${connected ? 'ok' : runState !== 'idle' ? 'warn' : ''}`;
  if (meta.decks) $('cConn').title = `Decks: ${meta.decks.join(', ') || 'none'}`;
  $('cLh').classList.toggle('off', !connected);
  $('bStop').disabled = runState === 'idle';
  $('bRun').disabled = runState === 'fly';
  $('bSim').disabled = runState === 'fly';
  renderChecks();
}
let lastStatus = null;

function handle(ev) {
  switch (ev.type) {
    case 'pose': handlePose(ev); break;
    case 'lh': handleLh(ev); break;
    case 'geos': geos = ev.bs; buildBaseStations(); break;
    case 'meta': handleMeta(ev.meta); break;
    case 'log': log(ev.text, ev.level === 'err' ? 'err' : ''); syncOnPrint(ev.text); break;
    case 'reset': clearTrail(); break;
    case 'check': handleCheck(ev); break;
    case 'checks_reset': checks = {}; renderChecks(); break;
  }
}

/* ---------------------------------------------------------------- preflight */

const STEPS = { radio: 'Radio', drone: 'Drone', basestations: 'Base stations', ready: 'Ready' };
let checks = {};

function checksPassed() {
  return Object.keys(STEPS).every((k) => ['ok', 'warn'].includes(checks[k]?.state));
}

function handleCheck(ev) {
  if (ev.only_if && checks[ev.step]?.state !== ev.only_if) return;
  checks[ev.step] = { state: ev.state, detail: ev.detail || '' };
  if (['ok', 'warn', 'fail'].includes(ev.state)) {
    log(`${STEPS[ev.step]}: ${ev.state}${ev.detail ? ` · ${ev.detail}` : ''}`,
      ev.state === 'fail' ? 'err' : ev.state === 'warn' ? 'warn' : 'sys');
  }
  renderChecks();
}

let pfOpen = false;
let pfStarted = false; // the check process for this dialog has reported at least once

function renderChecks() {
  for (const el of document.querySelectorAll('#preflight .step')) {
    const c = checks[el.dataset.step];
    el.className = `step ${c?.state || 'pending'}`;
    el.querySelector('span').textContent = c?.state === 'running' ? 'checking…' : c?.detail || '';
    el.title = c?.detail || '';
  }
  const failed = Object.values(checks).some((c) => c.state === 'fail');
  if (Object.keys(checks).length) pfStarted = true;
  const ended = pfStarted && runState !== 'check'; // check process exited before passing
  $('pfFly').disabled = !(runState === 'check' && checksPassed());
  $('pfRetry').hidden = !(failed || (ended && !checksPassed()));
}

function openPreflight() {
  pfOpen = true;
  pfStarted = false;
  checks = {};
  renderChecks();
  $('preflight').hidden = false;
  post('/api/run', { mode: 'check', code: editor.getValue() });
}

function closePreflight(disconnect) {
  pfOpen = false;
  $('preflight').hidden = true;
  if (disconnect && runState === 'check') post('/api/stop');
}

async function confirmFly() {
  $('pfFly').disabled = true;
  stopClock();
  clearTrail();
  const res = await post('/api/run', { mode: 'fly', code: editor.getValue() });
  if (res.ok) closePreflight(false);
  else renderChecks();
}

/* ----------------------------------------------------------------- console */

const consoleEl = $('console');
function log(text, cls = '') {
  const div = document.createElement('div');
  if (cls) div.className = cls;
  const t = document.createElement('span');
  t.className = 't';
  t.textContent = new Date().toLocaleTimeString([], { hour12: false });
  div.append(t, text);
  const stick = consoleEl.scrollTop + consoleEl.clientHeight >= consoleEl.scrollHeight - 4;
  consoleEl.appendChild(div);
  while (consoleEl.childElementCount > 1000) consoleEl.firstChild.remove();
  if (stick) consoleEl.scrollTop = consoleEl.scrollHeight;
}

/* ------------------------------------------------------------------ events */

function connectEvents() {
  const es = new EventSource('/events');
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === 'snapshot') {
      clearTrail();
      handleMeta(m.meta);
      geos = m.geos || {};
      checks = m.checks || {};
      renderChecks();
      buildBaseStations();
      if (m.lh) handleLh(m.lh);
      // Replay history into the trail cheaply; only the newest pose updates the rest of the UI
      m.poses.forEach((p) => addTrail(p.p, Math.hypot(...p.v), false));
      flushTrail();
      if (m.poses.length) handlePose(m.poses[m.poses.length - 1]);
      m.logs.forEach((l) => log(l.text, l.level === 'err' ? 'err' : ''));
    } else {
      m.events.forEach(handle);
    }
  };
  es.onerror = () => {
    $('cConn').querySelector('.dot').className = 'dot';
    $('cConn').title = 'server offline';
  };
  es.onopen = () => { $('cConn').title = 'Crazyflie radio address'; };
}

/* ------------------------------------------------------------- radio address */

const URI_RE = /^(\s*URI\s*=\s*)(["'])(.*?)\2/;

function showUri(uri) {
  const el = $('uri');
  if (document.activeElement !== el) el.value = uri || '';
  el.placeholder = uri ? '' : 'no URI = "..." in code';
  fitUri();
}

function fitUri() {
  const el = $('uri');
  el.style.width = `${Math.max(12, (el.value || el.placeholder).length + 1)}ch`;
}

// Typing in the header rewrites the URI = "..." line in the code (undoable in the editor)
$('uri').addEventListener('input', () => {
  fitUri();
  const value = $('uri').value.trim();
  for (let i = 0; i < editor.lineCount(); i++) {
    const m = editor.getLine(i).match(URI_RE);
    if (!m) continue;
    const from = { line: i, ch: m[1].length + 1 };
    editor.replaceRange(value, from, { line: i, ch: from.ch + m[3].length }, '+uri');
    return;
  }
});
$('uri').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
$('uri').addEventListener('blur', () => showUri(plan?.uri));

/* ---------------------------------------------------------------- controls */

async function post(url, body) {
  const res = await fetch(url, { method: 'POST', body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) log(data.error || `${url} failed`, 'err');
  return data;
}

async function save() {
  let name = $('fname').value.trim();
  if (name && !name.endsWith('.py')) name += '.py';
  const res = await post('/api/save', { code: editor.getValue(), name });
  if (res.ok) {
    $('fname').value = res.name;
    log(`saved ${res.path}`, 'sys');
  }
}
$('fname').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } });

$('bRun').addEventListener('click', openPreflight);
$('pfFly').addEventListener('click', confirmFly);
$('pfCancel').addEventListener('click', () => closePreflight(true));
$('pfRetry').addEventListener('click', openPreflight);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && pfOpen) closePreflight(true); });
$('bSim').addEventListener('click', () => (clock?.kind === 'sim' ? stopClock() : startSim()));
$('bStop').addEventListener('click', () => post('/api/stop'));
$('bSave').addEventListener('click', save);
$('bClear').addEventListener('click', clearTrail);
$('bLayers').addEventListener('click', (e) => { e.stopPropagation(); $('layers').hidden = !$('layers').hidden; });
$('layers').addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => { $('layers').hidden = true; });
$('scale').addEventListener('input', (e) => setScale(Number(e.target.value)));

$('bCoords').addEventListener('click', () => {
  const on = $('bCoords').classList.toggle('on');
  document.body.classList.toggle('nocoords', !on);
  try { localStorage.setItem('viz.coords', on ? '1' : '0'); } catch {}
});
try {
  if (localStorage.getItem('viz.coords') === '0') {
    $('bCoords').classList.remove('on');
    document.body.classList.add('nocoords');
  }
} catch {}

document.querySelectorAll('[data-layer]').forEach((el) => el.addEventListener('change', applyLayers));
$('layers').querySelector('[data-layer="plan"]').id = 'layerPlan';

function layer(name) {
  return document.querySelector(`[data-layer="${name}"]`).checked;
}
function applyLayers() {
  planGroup.visible = layer('plan');
  if (!planGroup.visible) ghost.visible = false;
  trailGroup.visible = layer('trail');
  for (const o of Object.values(bsObjs)) o.fov.visible = layer('fov');
  document.body.classList.toggle('nolabels', !layer('labels'));
}

/* ------------------------------------------------------------------ camera */

let camAnim = null;
function flyCamera(target, position) {
  camAnim = { t0: performance.now(), fromT: controls.target.clone(), fromP: camera.position.clone(), target, position };
}

function sceneBounds() {
  const b = grid;
  const zmax = Math.max(1, ...bsPositions().map((p) => p[2]));
  return { cx: (b.x0 + b.x1) / 2, cy: (b.y0 + b.y1) / 2, span: Math.max(b.x1 - b.x0, b.y1 - b.y0), zmax };
}

function setView(v) {
  const { cx, cy, span, zmax } = sceneBounds();
  const zc = Math.min(zmax / 2, 0.8);
  const fitDist = (span / 2) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.1;
  if (v === 'top') flyCamera(new THREE.Vector3(cx, cy, 0), new THREE.Vector3(cx, cy - 0.001, fitDist + zmax));
  else if (v === 'side') flyCamera(new THREE.Vector3(cx, cy, zc), new THREE.Vector3(cx, cy - fitDist, zc));
  else flyCamera(new THREE.Vector3(cx, cy, zc * 0.6),
    new THREE.Vector3(cx - fitDist * 0.55, cy - fitDist * 0.75, zc + fitDist * 0.55));
}
document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

/* ------------------------------------------------------------------ layout */

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  renderer.setSize(w, h);
  labelRenderer.setSize(w, h);
  trailMat.resolution.set(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(host);

const divider = $('divider');
divider.addEventListener('pointerdown', (e) => {
  divider.setPointerCapture(e.pointerId);
  divider.classList.add('drag');
  const move = (ev) => {
    const pct = Math.min(75, Math.max(20, (ev.clientX / window.innerWidth) * 100));
    document.documentElement.style.setProperty('--left', `${pct}%`);
    editor.refresh();
  };
  const up = () => {
    divider.classList.remove('drag');
    divider.removeEventListener('pointermove', move);
    divider.removeEventListener('pointerup', up);
  };
  divider.addEventListener('pointermove', move);
  divider.addEventListener('pointerup', up);
});

// Drag the bar above the output panel to resize it
const hdivider = $('hdivider');
try { const h = localStorage.getItem('viz.console'); if (h) document.documentElement.style.setProperty('--console', h); } catch {}
hdivider.addEventListener('pointerdown', (e) => {
  hdivider.setPointerCapture(e.pointerId);
  hdivider.classList.add('drag');
  const left = $('left').getBoundingClientRect();
  let h = null;
  const move = (ev) => {
    h = `${Math.round(Math.min(left.height * 0.8, Math.max(40, left.bottom - ev.clientY)))}px`;
    document.documentElement.style.setProperty('--console', h);
    editor.refresh();
  };
  const up = () => {
    hdivider.classList.remove('drag');
    hdivider.removeEventListener('pointermove', move);
    hdivider.removeEventListener('pointerup', up);
    try { if (h) localStorage.setItem('viz.console', h); } catch {}
  };
  hdivider.addEventListener('pointermove', move);
  hdivider.addEventListener('pointerup', up);
});

// LH status dot lives inside the chip
const lhdot = document.createElement('i');
lhdot.id = 'lhdot';
lhdot.className = 'dot';
$('cLh').prepend(lhdot);

/* -------------------------------------------------------------------- loop */

function animate() {
  requestAnimationFrame(animate);

  if (camAnim) {
    const u = Math.min(1, (performance.now() - camAnim.t0) / 600);
    const k = 1 - Math.pow(1 - u, 3);
    controls.target.lerpVectors(camAnim.fromT, camAnim.target, k);
    camera.position.lerpVectors(camAnim.fromP, camAnim.position, k);
    if (u >= 1) camAnim = null;
  }
  controls.update();

  if (drone.visible) {
    const p = drone.position;
    dropGeom.attributes.position.setXYZ(0, p.x, p.y, p.z);
    dropGeom.attributes.position.setXYZ(1, p.x, p.y, 0);
    dropGeom.attributes.position.needsUpdate = true;
    dropLine.computeLineDistances();
    floorRing.position.set(p.x, p.y, 0.001);
    dropLine.visible = floorRing.visible = p.z > 0.02;
    for (const [id, o] of Object.entries(bsObjs)) {
      const rx = connected && lh && ((lh.receive >> id) & 1);
      o.ray.visible = !!rx && layer('rays');
      if (o.ray.visible) {
        o.ray.geometry.attributes.position.setXYZ(1, p.x, p.y, p.z);
        o.ray.geometry.attributes.position.needsUpdate = true;
      }
    }
  } else {
    dropLine.visible = floorRing.visible = false;
    for (const o of Object.values(bsObjs)) o.ray.visible = false;
  }
  updateClock();

  const w = host.clientWidth, h = host.clientHeight;
  renderer.setViewport(0, 0, w, h);
  renderer.clear();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);

  gCam.position.set(0, 0, 3).applyQuaternion(camera.quaternion);
  gCam.quaternion.copy(camera.quaternion);
  renderer.clearDepth();
  renderer.setScissorTest(true);
  renderer.setScissor(0, 0, GIZMO, GIZMO);
  renderer.setViewport(0, 0, GIZMO, GIZMO);
  renderer.render(gScene, gCam);
  renderer.setScissorTest(false);

  drawAttitude();
}

/* -------------------------------------------------------------------- boot */

(async () => {
  const { name, code } = await (await fetch('/api/code')).json();
  $('fname').value = name;
  editor.setValue(code);
  editor.clearHistory();
  editor.on('change', schedulePlan);
  await fetchPlan();
  resize();
  setView('iso');
  camAnim && Object.assign(camAnim, { t0: performance.now() - 600 });
  connectEvents();
  animate();
})();

// Console hook for testing without hardware, e.g. __viz.handle({type: 'pose', p: [0, 0, 0.5], v: [0, 0, 0], q: [0, 0, 0, 1]})
window.__viz = { handle };
