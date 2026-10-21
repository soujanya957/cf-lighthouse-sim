// Multi-drone dashboard for webGUI exports. The scene, model loading, base stations and camera controls come from
// ../static/app.js.
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
const GIZMO = 96;
const COLORS = ['#0a66ff', '#e8590c', '#1a9f5b', '#9c36b5', '#d6336c', '#0c8599', '#f08c00', '#5c7cfa'];
const STEPS = ['link', 'drone', 'decks', 'basestations', 'estimate', 'start', 'geometry'];
const STEP_NAMES = { link: 'Link', drone: 'Drone', decks: 'Decks', basestations: 'Base stations', estimate: 'Estimate',
  start: 'Start position', geometry: 'Same geometry' };
const PHASE_TEXT = { idle: 'not connected', starting: 'starting…', connecting: 'connecting…', checking: 'preflight…',
  ready: 'ready to fly', failed: 'preflight failed', flying: 'flying', landing: 'landing', done: 'landed', closed: 'not connected' };
const TRAIL_MAX = 20000;

/* ------------------------------------------------------------------ editor */

const editor = CodeMirror($('editor'), { mode: 'python', lineNumbers: true, readOnly: true, indentUnit: 4 });

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
controls.target.set(0, 0, 0.4);
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
  if (grid && b.x0 === grid.x0 && b.x1 === grid.x1 && b.y0 === grid.y0 && b.y1 === grid.y1) return;
  buildGrid(b);
}
buildGrid({ x0: -2, x1: 2, y0: -2, y1: 2 });

const originGroup = new THREE.Group();
[[1, 0, 0, 0xe0312b, 'x'], [0, 1, 0, 0x1a9f5b, 'y'], [0, 0, 1, 0x2f6bff, 'z']].forEach(([x, y, z, c, n]) => {
  originGroup.add(new THREE.ArrowHelper(new THREE.Vector3(x, y, z), new THREE.Vector3(), 0.5, c, 0.07, 0.035));
  label(n.toUpperCase(), `axis ${n}`, originGroup, [x * 0.6, y * 0.6, z * 0.6]);
});
originGroup.add(new THREE.Mesh(new THREE.SphereGeometry(0.018, 16, 12), new THREE.MeshBasicMaterial({ color: 0x000000 })));
scene.add(originGroup);

/* ------------------------------------------------------------------- fence */

const fenceGroup = new THREE.Group();
scene.add(fenceGroup);
let fleet = null;

function buildFence() {
  disposeGroup(fenceGroup);
  if (!fleet) return;
  const { lo, hi } = fleet.fence;
  const c = [];
  for (const z of [lo[2], hi[2]]) {
    c.push(lo[0], lo[1], z, hi[0], lo[1], z, hi[0], lo[1], z, hi[0], hi[1], z,
           hi[0], hi[1], z, lo[0], hi[1], z, lo[0], hi[1], z, lo[0], lo[1], z);
  }
  for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) c.push(x, y, lo[2], x, y, hi[2]);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(c, 3));
  fenceGroup.add(new THREE.LineSegments(g, new THREE.LineDashedMaterial({ color: 0xb0b0b5, dashSize: 0.06, gapSize: 0.05 })));
  fenceGroup.children[0].computeLineDistances();
  label('fence', 'h', fenceGroup, [hi[0], hi[1], hi[2]]);
}

/* ------------------------------------------------------------------ models */

async function loadURDF(name) {
  const xml = await (await fetch(`urdf/${name}.urdf.xacro`)).text();
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  const loader = new ColladaLoader();
  const parts = [];
  for (const visual of doc.querySelectorAll('link > visual')) {
    const mesh = visual.querySelector('geometry > mesh');
    if (!mesh) continue;
    const url = mesh.getAttribute('filename').replace(/^package:\/\/[^/]+\//, 'urdf/');
    const obj = (await loader.loadAsync(url)).scene;
    obj.rotation.set(0, 0, 0); // URDF is already Z-up; undo the loader's Y-up conversion
    const wrap = new THREE.Group();
    const origin = visual.querySelector('origin');
    if (origin) {
      wrap.position.set(...(origin.getAttribute('xyz') || '0 0 0').trim().split(/\s+/).map(Number));
      const rpy = (origin.getAttribute('rpy') || '0 0 0').trim().split(/\s+/).map(Number);
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
  return [g];
}

const modelParts = loadURDF('crazyflie').catch((e) => { console.warn('URDF load failed', e); return fallbackModel(); });
let modelScale = 3;

// One drone in the scene: the model, a colored halo, the LED ring color, a name label and a drop line to the floor
function makeDrone(color, name, ghost) {
  const group = new THREE.Group();
  const model = new THREE.Group();
  group.add(model);
  const halo = new THREE.Mesh(new THREE.RingGeometry(0.05, 0.064, 40),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: ghost ? 0.45 : 0.95, side: THREE.DoubleSide, depthWrite: false }));
  model.add(halo);
  const led = new THREE.Mesh(new THREE.SphereGeometry(0.011, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  led.position.z = -0.012;
  led.visible = false;
  model.add(led);
  if (!ghost) model.add(new THREE.AxesHelper(0.05));
  modelParts.then((parts) => {
    const mat = ghost ? new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.3, depthWrite: false }) : null;
    for (const p of parts) {
      const c = p.clone(true);
      if (mat) c.traverse((o) => { if (o.isMesh) o.material = mat; });
      model.add(c);
    }
  });
  model.scale.setScalar(modelScale);
  const tag = label(ghost ? `${name} sim` : name, 'cf', group, [0, 0, 0.09]);
  tag.element.style.background = color;
  if (ghost) tag.element.style.opacity = '0.7';
  const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
    new THREE.LineDashedMaterial({ color, dashSize: 0.03, gapSize: 0.02, transparent: true, opacity: 0.6 }));
  drop.frustumCulled = false;
  group.visible = false;
  drop.visible = false;
  scene.add(group, drop);
  return { group, model, led, drop, tag };
}

function placeDrone(obj, p, rpy) {
  obj.group.position.set(p[0], p[1], p[2]);
  if (rpy) obj.model.rotation.set(rpy[0], rpy[1], rpy[2], 'ZYX');
  const a = obj.drop.geometry.attributes.position;
  a.setXYZ(0, p[0], p[1], p[2]);
  a.setXYZ(1, p[0], p[1], 0);
  a.needsUpdate = true;
  obj.drop.computeLineDistances();
}

function showDrone(obj, on) {
  obj.group.visible = on;
  obj.drop.visible = on && obj.group.position.z > 0.03;
}

function setLed(obj, rgb) {
  const lit = rgb && rgb.some((v) => v > 0);
  obj.led.visible = !!lit;
  if (lit) obj.led.material.color.setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255);
}

function setScale(s) {
  modelScale = s;
  for (const r of robots) { r.live.model.scale.setScalar(s); r.ghost.model.scale.setScalar(s); }
  $('scaleval').textContent = `${s}×`;
}

/* ------------------------------------------------------------------ robots */

let project = null;   // selected project (from /api/project)
let robots = [];      // per robot of the project: scene objects and live state
const robotsGroup = new THREE.Group();   // start markers and planned paths
scene.add(robotsGroup);
const trailMats = [];

function prefixOf(r) { return r.id != null ? `cf${r.id}` : null; }
function robotByPrefix(prefix) { return robots.find((r) => prefixOf(r) === prefix); }

function clearRobots() {
  for (const r of robots) {
    for (const o of [r.live, r.ghost]) {
      disposeGroup(o.group);
      scene.remove(o.group, o.drop);
      o.drop.geometry.dispose();
    }
    r.trail.line.geometry.dispose();
    trailGroup.remove(r.trail.line);
  }
  disposeGroup(robotsGroup);
  trailMats.splice(0).forEach((m) => m.dispose());
  robots = [];
}

function buildRobots() {
  clearRobots();
  if (!project) return;
  const ids = savedIds(project);
  project.robots.forEach((rb, i) => {
    const color = COLORS[i % COLORS.length];
    const r = { index: i, name: rb.name, start: rb.start, color, id: ids[i], group: groupOf(i) };
    r.live = makeDrone(color, `cf${r.id ?? '?'}`, false);
    r.ghost = makeDrone(color, `cf${r.id ?? '?'}`, true);
    const mat = new LineMaterial({ color, linewidth: 3, worldUnits: false });
    trailMats.push(mat);
    const line = new Line2(new LineGeometry(), mat);
    line.frustumCulled = false;
    trailGroup.add(line);
    r.trail = { line, pts: [], dirty: false };
    // Start marker on the floor
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.07, 0.09, 4, 1, Math.PI / 4),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
    ring.position.set(rb.start[0], rb.start[1], 0.002);
    robotsGroup.add(ring);
    const guide = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color, dashSize: 0.05, gapSize: 0.04 }));
    guide.visible = false;
    guide.frustumCulled = false;
    robotsGroup.add(guide);
    r.guide = guide;
    label(rb.name, 'start', robotsGroup, [rb.start[0], rb.start[1] - 0.16, 0]);
    robots.push(r);
  });
  resizeTrails();
  fitGrid([...project.robots.map((r) => r.start), ...bsPositions()]);
  renderRobots();
}

function groupOf(i) {
  return Object.entries(project.groups).filter(([, idx]) => idx.includes(i)).map(([g]) => g).join(', ');
}

function savedIds(p) {
  let ids = null;
  try { ids = JSON.parse(localStorage.getItem(`lh.ids.${p.name}`)); } catch {}
  if (Array.isArray(ids) && ids.length === p.robots.length) return ids;
  const fleetIds = Object.keys(fleet?.drones || {}).map(Number).sort((a, b) => a - b);
  return p.robots.map((_, i) => fleetIds[i] ?? i + 1);
}

function currentIds() { return robots.map((r) => r.id); }

// The URI a drone ID connects on: listed in the fleet file, or made from its uri_template
function uriFor(id) {
  if (id == null) return { uri: null };
  if (fleet?.drones?.[id]) return { uri: fleet.drones[id], fromTemplate: false };
  const t = fleet?.template;
  if (!t) return { uri: null };
  return { uri: t.replace(/\{id:02X\}/g, id.toString(16).toUpperCase().padStart(2, '0')).replace(/\{id\}/g, String(id)),
    fromTemplate: true };
}

function renderFleetIds() {
  let list = $('fleetIds');
  if (!list) { list = document.createElement('datalist'); list.id = 'fleetIds'; document.body.append(list); }
  list.innerHTML = '';
  for (const [id, uri] of Object.entries(fleet?.drones || {})) list.append(new Option(uri, id));
}

function idsValid() {
  const ids = currentIds();
  return ids.every((v) => Number.isInteger(v) && v > 0) && new Set(ids).size === ids.length;
}

function setId(r, value) {
  const v = Number(value);
  r.id = Number.isInteger(v) && v > 0 ? v : null;
  for (const o of [r.live, r.ghost]) o.tag.element.textContent = `cf${r.id ?? '?'}${o === r.ghost ? ' sim' : ''}`;
  try { localStorage.setItem(`lh.ids.${project.name}`, JSON.stringify(currentIds())); } catch {}
  sim = null; // a recording is for one set of IDs
  simError = null;
  updateSim();
  renderControls();
  if (idsValid()) simulate(false);
}

/* --------------------------------------------------------- robots table */

const fmt = (p) => p.map((v) => (Math.round(v * 100) / 100).toString()).join(', ');

function stateText(st) {
  if (!st) return ['—', ''];
  if (st.killed) return ['motors cut', 'state-bad'];
  for (const bad of ['crashed', 'locked', 'tumbled']) if (st.supervisor?.includes(bad)) return [bad, 'state-bad'];
  if (st.grounded && st.flying) return ['landing', 'state-flying'];
  if (st.supervisor?.includes('flying') || st.flying) return ['flying', 'state-flying'];
  if (st.supervisor?.includes('armed')) return ['armed', ''];
  return ['on ground', ''];
}

function bitCount(mask) { let n = 0; for (let i = 0; i < 16; i++) if (mask && (mask >> i) & 1) n++; return n; }

// One line for a drone: the preflight result while connected, else its flight state
function statusBadge(r) {
  const checks = (session && hubChecks[prefixOf(r)]) || {};
  const running = STEPS.find((s) => checks[s]?.state === 'running');
  if (running) return ['busy', `checking ${STEP_NAMES[running].toLowerCase()}…`];
  const st = session && hubStatus[prefixOf(r)];
  if (st && (flightPhase() || phase.phase === 'done')) {
    const [text, cls] = stateText(st);
    return [cls === 'state-bad' ? 'fail' : cls === 'state-flying' ? 'busy' : 'idle', text];
  }
  const fail = STEPS.find((s) => checks[s]?.state === 'fail');
  if (fail) return ['fail', `✗ ${STEP_NAMES[fail]}`];
  if (!session) return ['idle', 'not connected'];
  if (['connecting', 'starting'].includes(phase.phase)) return ['busy', 'connecting…'];
  const warn = STEPS.find((s) => checks[s]?.state === 'warn');
  if (phase.phase === 'ready') return [warn ? 'warn' : 'ok', warn ? `✓ ready (${STEP_NAMES[warn].toLowerCase()} warning)` : '✓ ready'];
  return ['idle', 'connected'];
}

function renderRobots() {
  const tbody = $('robots').querySelector('tbody');
  tbody.innerHTML = '';
  const locked = connected();
  for (const r of robots) {
    const st = session && hubStatus[prefixOf(r)];
    const checks = (session && hubChecks[prefixOf(r)]) || {};
    const tr = document.createElement('tr');
    const cells = Array.from({ length: 5 }, () => document.createElement('td'));
    cells[0].innerHTML = `<i class="sw" style="background:${r.color}"></i>`;
    cells[1].innerHTML = `<b></b><br><small class="muted"></small>`;
    cells[1].querySelector('b').textContent = r.name;
    cells[1].querySelector('small').textContent = r.group;
    const input = document.createElement('input');
    input.value = r.id ?? '';
    input.inputMode = 'numeric';
    input.disabled = locked;
    input.setAttribute('list', 'fleetIds');
    input.title = 'Crazyflie ID: its radio address comes from Drones (top right)';
    input.placeholder = 'ID';
    const dup = currentIds().filter((v) => v === r.id).length > 1;
    input.classList.toggle('bad', r.id == null || dup);
    input.addEventListener('change', () => { setId(r, input.value); renderRobots(); });
    const uri = document.createElement('div');
    const u = uriFor(r.id);
    uri.className = `uri ${u.uri ? '' : 'bad'}`;
    uri.textContent = u.uri ? u.uri.replace('radio://', '') + (u.fromTemplate ? ' *' : '') : 'no address: add it in Drones';
    uri.title = u.fromTemplate ? 'Not listed in Drones: address made from uri_template in the fleet file' : (u.uri || '');
    cells[2].append(input, uri);

    const [cls, text] = statusBadge(r);
    cells[3].innerHTML = `${cls === 'busy' ? '<i class="spin"></i>' : ''}<span class="badge ${cls}"></span><br><small class="muted"></small>`;
    cells[3].querySelector('.badge').textContent = text;
    cells[3].title = STEPS.filter((s) => checks[s]).map((s) => `${STEP_NAMES[s]}: ${checks[s].state}${checks[s].detail ? ` · ${checks[s].detail}` : ''}`).join('\n');
    if (st) {
      const bits = [];
      if (st.vbat != null) bits.push(`${st.vbat.toFixed(2)} V`);
      if (st.link != null) bits.push(`link ${st.link}%`);
      bits.push(`${bitCount(st.receive)} base stations`);
      const small = cells[3].querySelector('small');
      small.textContent = bits.join(' · ');
      if (st.vbat != null && st.vbat < 3.7) small.className = 'low';
    }
    r.posCell = cells[4];
    renderPosition(r);
    tr.append(...cells);
    tbody.append(tr);
    // The first failure or warning, spelled out under the row
    const bad = STEPS.map((s) => [s, checks[s]]).find(([, c]) => c?.state === 'fail')
      || STEPS.map((s) => [s, checks[s]]).find(([, c]) => c?.state === 'warn');
    if (bad) {
      const d = document.createElement('tr');
      d.className = `detail ${bad[1].state}`;
      const td = document.createElement('td');
      td.colSpan = 5;
      td.textContent = `${STEP_NAMES[bad[0]]}: ${bad[1].detail}`;
      d.append(td);
      tbody.append(d);
    }
  }
}

/* --------------------------------------------------------------- placement */

// Placing the drones: connected and not flying
function placing() { return connected() && !flightPhase() && phase.phase !== 'done'; }

// How far a drone sits from its start spot on the floor, and which way to move it
function placement(r) {
  if (!r.livePose || !r.live.group.visible) return null;
  const dx = r.start[0] - r.livePose[0], dy = r.start[1] - r.livePose[1];
  const off = Math.hypot(dx, dy);
  return { dx, dy, off, ok: off <= (fleet?.startTolerance ?? 0.3) };
}

// "✓ on its spot", or the move to its start spot, e.g. "✗ 0.29 m off: move +0.12 x, +0.26 y"
function placeText(pl) {
  if (pl.off < 0.05) return '✓ on its spot';
  const parts = [['x', pl.dx], ['y', pl.dy]].filter(([, v]) => Math.abs(v) >= 0.02)
    .map(([a, v]) => `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(2)} ${a}`);
  return `${pl.ok ? '✓' : '✗'} ${pl.off.toFixed(2)} m off: move ${parts.join(', ')}`;
}

function renderPosition(r) {
  const el = r.posCell;
  if (!el) return;
  const pl = placing() ? placement(r) : null;
  if (pl) {
    el.className = `pos ${pl.ok ? 'ok' : 'off'}`;
    el.textContent = placeText(pl);
    el.title = `start (${fmt(r.start)}), now (${fmt(r.livePose)})`;
  } else if (r.livePose && r.live.group.visible) {
    el.className = 'pos';
    el.textContent = fmt(r.livePose);
    el.title = 'live position';
  } else {
    el.className = 'pos';
    el.textContent = `start ${fmt(r.start)}`;
    el.title = 'start position from starting_positions.yaml';
  }
}

// While placing: a dashed line on the floor from where the drone is to its start spot
function updateGuide(r) {
  const pl = placing() ? placement(r) : null;
  r.guide.visible = !!pl && pl.off > 0.02;
  if (!r.guide.visible) return;
  const pos = r.guide.geometry.attributes.position;
  pos.setXYZ(0, r.livePose[0], r.livePose[1], 0.004);
  pos.setXYZ(1, r.start[0], r.start[1], 0.004);
  pos.needsUpdate = true;
  r.guide.computeLineDistances();
  r.guide.geometry.computeBoundingSphere();
}

/* ------------------------------------------------------------------ trails */

const trailGroup = new THREE.Group();
scene.add(trailGroup);

function resizeTrails() {
  for (const m of trailMats) m.resolution.set(host.clientWidth, host.clientHeight);
}

function addTrail(r, p) {
  const pts = r.trail.pts;
  const n = pts.length;
  if (n && Math.hypot(p[0] - pts[n - 3], p[1] - pts[n - 2], p[2] - pts[n - 1]) < 0.004) return;
  pts.push(p[0], p[1], p[2]);
  if (pts.length > TRAIL_MAX * 3) pts.splice(0, pts.length - TRAIL_MAX * 3);
  r.trail.dirty = true;
}

function flushTrails() {
  for (const r of robots) {
    if (!r.trail.dirty) continue;
    r.trail.dirty = false;
    if (r.trail.pts.length < 6) continue;
    const g = new LineGeometry();
    g.setPositions(r.trail.pts);
    r.trail.line.geometry.dispose();
    r.trail.line.geometry = g;
  }
}

function clearTrails() {
  for (const r of robots) { r.trail.pts = []; r.trail.line.geometry.dispose(); r.trail.line.geometry = new LineGeometry(); }
}

/* ------------------------------------------------------------- simulation */

let sim = null;       // { key, data, summary, problems, duration }
let simT = 0;
let playing = false;
let follow = null;    // wall-clock show start (s) while a real flight runs: the simulation follows it
let lastFrame = performance.now();
const planGroup = new THREE.Group();
scene.add(planGroup);

function simKey() { return `${project?.name}|${currentIds().join(',')}`; }

let simulating = false;
let simError = null;

async function simulate(play) {
  if (!project || !idsValid()) { log('give every robot its own Crazyflie ID first', 'err'); return; }
  const key = simKey();
  if (sim?.key === key) { if (play) togglePlay(true); return; }
  simulating = true;
  simError = null;
  renderControls();
  try {
    const res = await post('/api/simulate', { project: project.name, ids: currentIds() });
    if (simKey() !== key) return; // the selection changed meanwhile
    if (!res.ok) {
      sim = null;
      simError = res.error;
      updateSim();
      return;
    }
    const data = res.sim;
    sim = { key, data, summary: res.summary, problems: res.problems, duration: data.times.at(-1) || 0 };
    res.output?.forEach((l) => log(l));
    showSummary(sim);
    simT = 0;
    buildPlan();
    updateSim();
    if (play) togglePlay(true);
  } finally {
    simulating = false;
    renderControls();
  }
}

function showSummary(s, error) {
  const el = $('simSummary');
  el.hidden = false;
  el.innerHTML = '';
  if (error) {
    const d = document.createElement('div');
    d.className = 'err';
    d.textContent = error;
    el.append(d);
    return;
  }
  const sum = s.summary;
  const sep = sum.min_separation;
  const line = document.createElement('div');
  line.textContent = `${sum.duration.toFixed(1)} s · ` +
    (sep ? `closest ${sep.distance} m (${sep.pair.join('–')} at ${sep.time} s)` : 'one drone airborne at a time') +
    (s.problems.length ? '' : ' · no fence violations or close calls');
  el.append(line);
  for (const p of s.problems) {
    const d = document.createElement('div');
    d.className = 'warn';
    d.textContent = `⚠ ${p}`;
    el.append(d);
  }
}

function buildPlan() {
  disposeGroup(planGroup);
  if (!sim) return;
  const all = [];
  for (const d of sim.data.drones) {
    const r = robotByPrefix(d.cf);
    if (!r || d.p.length < 2) continue;
    const g = new THREE.BufferGeometry().setFromPoints(d.p.map((p) => new THREE.Vector3(...p)));
    const line = new THREE.Line(g, new THREE.LineDashedMaterial({ color: r.color, dashSize: 0.04, gapSize: 0.03 }));
    line.computeLineDistances();
    planGroup.add(line);
    all.push(...d.p);
  }
  fitGrid([...all, ...project.robots.map((r) => r.start), ...bsPositions()]);
  applyLayers();
}

function sampleIndex(t) {
  const times = sim.data.times;
  let lo = 0, hi = times.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t) lo = mid; else hi = mid - 1; }
  return lo;
}

function simAt(d, t) {
  const times = sim.data.times;
  const i = sampleIndex(t);
  const j = Math.min(i + 1, times.length - 1);
  const u = j > i ? Math.min(1, Math.max(0, (t - times[i]) / (times[j] - times[i]))) : 0;
  const p = d.p[i].map((v, k) => v + (d.p[j][k] - v) * u);
  let led = null;
  for (const [lt, c] of d.leds) { if (lt <= t) led = c; else break; }
  return { p, led };
}

function updateSim() {
  $('simbar').hidden = !sim;
  if (!sim) {
    disposeGroup(planGroup);
    if (simError) showSummary(null, simError);
    $('simSummary').hidden = !simError;
    for (const r of robots) showDrone(r.ghost, false);
    playing = false;
    return;
  }
  simT = Math.min(Math.max(simT, 0), sim.duration);
  $('scrub').max = sim.duration;
  $('scrub').value = simT;
  $('simTime').textContent = `${simT.toFixed(1)} / ${sim.duration.toFixed(1)} s`;
  $('bPlay').textContent = playing ? '❚❚' : '▶';
  $('bSim').classList.toggle('active', playing);
  const showGhosts = layer('plan') && (playing || simT > 0 || follow != null);
  for (const d of sim.data.drones) {
    const r = robotByPrefix(d.cf);
    if (!r) continue;
    const s = simAt(d, simT);
    placeDrone(r.ghost, s.p);
    setLed(r.ghost, s.led);
    r.simPose = s;
    showDrone(r.ghost, showGhosts);
  }
}

function togglePlay(on) {
  if (!sim) return;
  playing = on ?? !playing;
  if (playing && simT >= sim.duration - 1e-6) simT = 0;
  lastFrame = performance.now();
  updateSim();
  renderControls();
}

function stepSim(now) {
  if (follow != null && sim) {
    simT = Math.min(Date.now() / 1000 - follow, sim.duration);
    updateSim();
  } else if (playing && sim) {
    simT += ((now - lastFrame) / 1000) * Number($('speed').value);
    if (simT >= sim.duration) { simT = sim.duration; playing = false; renderControls(); }
    updateSim();
  }
  lastFrame = now;
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

    const lbl = label(`${Number(id) + 1}`, 'bs', bsGroup, [o[0], o[1], o[2] + 0.11]);
    const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...o), new THREE.Vector3(o[0], o[1], 0)]),
      new THREE.LineDashedMaterial({ color: 0xb0b0b5, dashSize: 0.05, gapSize: 0.04 }));
    drop.computeLineDistances();
    bsGroup.add(drop);
    label(`${o[2].toFixed(2)} m`, 'h', bsGroup, [o[0], o[1], o[2] / 2]);
    bsObjs[id] = { fov, lbl, origin: o, rays: {} };
  }
  applyLayers();
  fitGrid([...bsPositions(), ...(project?.robots.map((r) => r.start) || [])]);
  if (!userMoved) setView('iso');
  renderBsChips();
}

// A ray from each base station to each live drone that receives it
function updateRays() {
  for (const [id, bs] of Object.entries(bsObjs)) {
    const seen = new Set();
    for (const r of robots) {
      const st = hubStatus[prefixOf(r)];
      const on = layer('rays') && r.live.group.visible && st && ((st.receive >> Number(id)) & 1);
      let ray = bs.rays[r.index];
      if (!on) { if (ray) ray.visible = false; continue; }
      if (!ray) {
        ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...bs.origin), new THREE.Vector3()]),
          new THREE.LineBasicMaterial({ color: r.color, transparent: true, opacity: 0.35 }));
        ray.frustumCulled = false;
        bsGroup.add(ray);
        bs.rays[r.index] = ray;
      }
      const p = r.live.group.position;
      ray.geometry.attributes.position.setXYZ(1, p.x, p.y, p.z);
      ray.geometry.attributes.position.needsUpdate = true;
      ray.visible = true;
      seen.add(r.index);
    }
    bs.lbl.element.classList.toggle('rx', seen.size > 0);
  }
}

function renderBsChips() {
  const statuses = Object.values(hubStatus).filter(Boolean);
  const ids = new Set(Object.keys(geos).map(Number));
  for (const st of statuses) for (const m of [st.geoVal, st.receive]) for (let i = 0; i < 16; i++) if (m && (m >> i) & 1) ids.add(i);
  const box = $('lhbs');
  box.innerHTML = '';
  $('cLh').classList.toggle('off', !statuses.length && !ids.size);
  if (!ids.size) { box.textContent = '—'; return; }
  for (const i of [...ids].sort((a, b) => a - b)) {
    const el = document.createElement('i');
    el.textContent = i + 1;
    const geo = geos[i] || statuses.some((st) => (st.geoVal >> i) & 1);
    const rx = statuses.filter((st) => (st.receive >> i) & 1).length;
    if (geo) el.classList.add('geo');
    if (statuses.length && rx === statuses.length) el.classList.add('rx');
    else if (rx) el.classList.add('some');
    el.title = `BS${i + 1}: ${geo ? 'geometry known' : 'no geometry'}` +
      (statuses.length ? `, seen by ${rx} of ${statuses.length} drone(s)` : '');
    box.append(el);
  }
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
    gScene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), dir.clone()]),
      new THREE.LineBasicMaterial({ color })));
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

/* -------------------------------------------------------------- live state */

let session = null;     // {project, ids} of the hardware session, from the server
let phase = { phase: 'idle' };
let hubStatus = {};
let hubChecks = {};

function connected() { return session != null; }
function flightPhase() { return ['flying', 'landing'].includes(phase.phase); }

function handlePoses(ev) {
  for (const [prefix, d] of Object.entries(ev.drones)) {
    const r = robotByPrefix(prefix);
    if (!r || !session || session.project !== project?.name) continue;
    const rpy = d.a.map((v) => THREE.MathUtils.degToRad(v));
    // stateEstimate.pitch is logged with the opposite sign of a right-handed pitch
    placeDrone(r.live, d.p, [rpy[0], -rpy[1], rpy[2]]);
    r.livePose = d.p;
    showDrone(r.live, true);
    updateGuide(r);
    renderPosition(r);
    if (flightPhase()) addTrail(r, d.p);
  }
  if (phase.phase === 'failed') renderHint();
}

function handlePhase(ev) {
  const prev = phase.phase;
  phase = ev;
  if (ev.phase === 'flying' && prev !== 'flying') {
    clearTrails();
    // Play the simulation alongside the flight when it is of the same project and IDs
    if (sim && session && sim.key === `${session.project}|${session.ids.join(',')}`) { follow = ev.t0; playing = false; }
  }
  if (['done', 'closed', 'failed'].includes(ev.phase)) follow = null;
  if (ev.phase === 'done') log(`landed (${ev.reason})${ev.log ? ` · flight log ${ev.log}` : ''}`, ev.reason === 'finished' ? 'sys' : 'warn');
  if (ev.phase === 'landing' && ev.reason && ev.reason !== 'finished') log(`stopping: ${ev.reason}`, 'warn');
  if (ev.phase === 'ready') log('preflight passed: ready to fly', 'sys');
  if (ev.phase === 'failed') log(ev.reason || 'failed', 'err');
  for (const r of robots) { updateGuide(r); renderPosition(r); }
  if (ev.phase === 'closed') {
    for (const r of robots) showDrone(r.live, false);
    hubStatus = {};
  }
  renderControls();
  renderRobots();
  renderBsChips();
}

function handleSession(s) {
  session = s;
  if (s) { hubChecks = {}; hubStatus = {}; }
  if (!s) for (const r of robots) showDrone(r.live, false);
  renderControls();
  renderRobots();
  renderBsChips();
}

function handleCheck(ev) {
  (hubChecks[ev.cf] ??= {})[ev.step] = { state: ev.state, detail: ev.detail };
  if (['ok', 'warn', 'fail'].includes(ev.state)) {
    log(`${ev.cf} ${STEP_NAMES[ev.step] || ev.step}: ${ev.state}${ev.detail ? ` · ${ev.detail}` : ''}`,
      ev.state === 'fail' ? 'err' : ev.state === 'warn' ? 'warn' : 'sys');
  }
  renderRobots();
}

let statusRender = 0;
function handle(ev) {
  switch (ev.type) {
    case 'poses': handlePoses(ev); break;
    case 'status':
      hubStatus = ev.drones;
      // Status arrives twice a second; the table doesn't need more
      if (performance.now() - statusRender > 400) { statusRender = performance.now(); renderRobots(); renderBsChips(); }
      break;
    case 'geos': if (Object.keys(ev.bs).length) { geos = ev.bs; buildBaseStations(); } break;
    case 'check': handleCheck(ev); break;
    case 'phase':
      // A new preflight; the link result stays
      if (ev.phase === 'checking') for (const cf of Object.keys(hubChecks)) hubChecks[cf] = { link: hubChecks[cf].link };
      handlePhase(ev);
      break;
    case 'session': handleSession(ev.session); break;
    case 'log': log(ev.text, ev.level === 'err' ? 'err' : ''); break;
  }
}

/* ---------------------------------------------------------------- controls */

function renderControls() {
  const p = phase.phase;
  const sameProject = session && project && session.project === project.name &&
    session.ids.join(',') === currentIds().join(',');
  $('sessionText').textContent = connected()
    ? `${session.project} · ${session.ids.map((i) => `cf${i}`).join(', ')} · ${PHASE_TEXT[p] || p}`
    : PHASE_TEXT.idle;
  $('cSession').querySelector('.dot').className =
    `dot ${p === 'ready' ? 'ok' : p === 'failed' ? 'err' : flightPhase() ? 'busy' : connected() ? 'warn' : ''}`;

  const busy = ['starting', 'connecting', 'checking'].includes(p) || flightPhase();
  const simDone = sim?.key === simKey();
  $('bCheck').disabled = !project || !idsValid() || (connected() && busy);
  $('bCheck').querySelector('span').textContent = connected() && sameProject && ['ready', 'failed'].includes(p) ? 'Check again' : 'Check drones';
  $('bCheck').classList.toggle('done', connected() && sameProject && p === 'ready');
  $('bFly').disabled = !(connected() && sameProject && p === 'ready');
  $('bSim').disabled = simulating || !project || !idsValid();
  $('bSim').querySelector('span').textContent = simulating ? 'Simulating…' : 'Simulate';
  $('bSim').classList.toggle('done', simDone && !sim.problems.length);
  $('bStop').hidden = !connected();
  $('bStop').classList.toggle('live', flightPhase());
  $('bStop').textContent = p === 'landing' ? '■ Cut motors' : flightPhase() ? '■ Stop (land)' : 'Disconnect';
  renderHint();
}

// What to do next, in one line
function renderHint() {
  const p = phase.phase;
  const set = (text, cls = '') => { $('hint').textContent = text; $('hint').className = cls; };
  if (!project) return set('Put a webGUI export in lighthouse_runner/projects/ and press Refresh.');
  if (!idsValid()) return set('Give each robot its own Crazyflie ID.', 'err');
  if (p === 'flying') return set('Flying. Stop lands every drone.', 'busy');
  if (p === 'landing') return set('Landing. Press Cut motors only in an emergency.', 'busy');
  if (p === 'done') return set(`Landed (${phase.reason}). Disconnect, or Check drones to fly again.`, phase.reason === 'finished' ? 'ok' : 'err');
  if (['starting', 'connecting'].includes(p) && connected()) return set('Connecting to the drones…', 'busy');
  if (p === 'checking') return set('Running the preflight…', 'busy');
  const offSpot = connected() ? robots.filter((r) => { const pl = placement(r); return pl && !pl.ok; }) : [];
  if (p === 'failed' && offSpot.length) return set(`Move ${offSpot.map((r) => `cf${r.id}`).join(', ')} to the dashed lines' ends (Position column), then Check again.`, 'err');
  if (p === 'failed') return set('Preflight failed: fix the red items below, then Check again.', 'err');
  if (p === 'ready') return set('All checks passed. Clear the flight area, then Fly.', 'ok');
  if (simulating) return set('Simulating…', 'busy');
  if (simError) return set('The simulation failed: see the error above.', 'err');
  if (sim?.key === simKey() && sim.problems.length) return set('The simulation found problems (above). Fix the project, or check them before flying.', 'err');
  if (sim?.key === simKey()) return set('Simulated. Put each drone on its start square, switch them on, then Check drones.');
  return set('Simulate first to see the show.');
}

async function check() {
  if (!project || !idsValid()) return;
  const sameProject = session && session.project === project.name && session.ids.join(',') === currentIds().join(',');
  if (sameProject && ['ready', 'failed'].includes(phase.phase)) { await post('/api/check'); return; }
  log(`connecting to ${currentIds().map((i) => `cf${i}`).join(', ')}…`, 'sys');
  await post('/api/connect', { project: project.name, ids: currentIds() });
}

function openFly() {
  const names = robots.map((r) => `cf${r.id} (${r.name})`).join(', ');
  $('flyText').textContent = `Fly ${project.name} with ${names}? Make sure the flight area is clear.`;
  const warnings = [];
  if (!sim || sim.key !== simKey()) warnings.push('This project and set of IDs hasn\'t been simulated.');
  else warnings.push(...sim.problems);
  for (const r of robots) {
    for (const [s, c] of Object.entries(hubChecks[prefixOf(r)] || {})) {
      if (c.state === 'warn') warnings.push(`cf${r.id} ${STEP_NAMES[s] || s}: ${c.detail}`);
    }
  }
  $('flyWarnings').innerHTML = '';
  for (const w of warnings) { const li = document.createElement('li'); li.textContent = w; $('flyWarnings').append(li); }
  $('flyDialog').hidden = false;
  $('flyGo').focus();
}

function closeFly() { $('flyDialog').hidden = true; }

/* ------------------------------------------------------------------ drones */

const RADIO_RE = /^radio:\/\/(\d+)\/(\d+)\/(250K|1M|2M)\/([0-9A-Fa-f]{10})$/;

function openDrones() {
  $('droneTable').querySelector('tbody').innerHTML = '';
  $('dError').textContent = '';
  $('dNote').textContent = '';
  const entries = Object.entries(fleet?.drones || {}).sort((a, b) => a[0] - b[0]);
  for (const [id, uri] of entries) addDroneRow(Number(id), uri);
  if (!entries.length) addDroneRow();
  $('dronesDialog').hidden = false;
}

// One row: ID, then the URI's parts. A new row copies the previous row's radio, channel and rate.
function addDroneRow(id = null, uri = null) {
  const tbody = $('droneTable').querySelector('tbody');
  const last = [...tbody.querySelectorAll('tr')].at(-1);
  const m = uri ? uri.match(RADIO_RE) : null;
  const prev = last ? rowValues(last) : null;
  const tr = document.createElement('tr');
  tr.innerHTML = `<td><input class="id" inputmode="numeric" placeholder="73"></td>
    <td><input class="dongle" inputmode="numeric"></td>
    <td><input class="ch" inputmode="numeric"></td>
    <td><select class="rate"><option>2M</option><option>1M</option><option>250K</option></select></td>
    <td><input class="addr" spellcheck="false" maxlength="10" placeholder="E7E7E7E749"></td>
    <td><button class="small" title="Remove">✕</button></td>`;
  const q = (c) => tr.querySelector(`.${c}`);
  q('id').value = id ?? '';
  q('dongle').value = m ? m[1] : prev?.dongle ?? '0';
  q('ch').value = m ? m[2] : prev?.ch ?? '80';
  q('rate').value = m ? m[3] : prev?.rate ?? '2M';
  q('addr').value = m ? m[4].toUpperCase() : uri ?? '';
  if (uri && !m) q('addr').title = `not a radio URI: ${uri}`;
  // A new drone's address usually ends in its ID in hex (cf73 -> E7E7E7E749)
  q('id').addEventListener('change', () => {
    const v = Number(q('id').value);
    if (!q('addr').value && Number.isInteger(v) && v > 0 && v < 256) {
      q('addr').value = `E7E7E7E7${v.toString(16).toUpperCase().padStart(2, '0')}`;
    }
  });
  tr.querySelector('button').addEventListener('click', () => tr.remove());
  tbody.append(tr);
  if (id == null && !uri) q('id').focus();
}

function rowValues(tr) {
  const q = (c) => tr.querySelector(`.${c}`).value.trim();
  return { id: q('id'), dongle: q('dongle'), ch: q('ch'), rate: q('rate'), addr: q('addr').toUpperCase() };
}

async function saveDrones() {
  const drones = {};
  const errors = [];
  for (const tr of $('droneTable').querySelectorAll('tbody tr')) {
    const v = rowValues(tr);
    tr.querySelectorAll('input').forEach((i) => i.classList.remove('bad'));
    if (!v.id && !v.addr) continue; // empty row
    const bad = (c) => tr.querySelector(`.${c}`).classList.add('bad');
    const id = Number(v.id);
    if (!Number.isInteger(id) || id <= 0) { bad('id'); errors.push(`"${v.id}" isn't a drone ID`); continue; }
    if (drones[id]) { bad('id'); errors.push(`cf${id} is listed twice`); continue; }
    if (!/^\d+$/.test(v.dongle)) { bad('dongle'); errors.push(`cf${id}: radio must be 0, 1, …`); continue; }
    if (!/^\d+$/.test(v.ch) || Number(v.ch) > 125) { bad('ch'); errors.push(`cf${id}: channel must be 0–125`); continue; }
    if (!/^[0-9A-F]{10}$/.test(v.addr)) { bad('addr'); errors.push(`cf${id}: the address is 10 hex digits, like E7E7E7E749`); continue; }
    drones[id] = `radio://${Number(v.dongle)}/${Number(v.ch)}/${v.rate}/${v.addr}`;
  }
  if (errors.length) { $('dError').textContent = errors.join(' · '); return; }
  const res = await post('/api/fleet/drones', { drones });
  if (!res.ok) { $('dError').textContent = res.error; return; }
  fleet = res.fleet;
  renderFleetIds();
  renderRobots();
  $('dronesDialog').hidden = true;
  log(`saved ${Object.keys(drones).length} drone(s) to ${fleet.file}`, 'sys');
}

async function importCrazyflies(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const res = await post('/api/fleet/import', { text: await file.text() });
  if (!res.ok) { $('dError').textContent = res.error; return; }
  $('dError').textContent = '';
  // Imported drones replace rows with the same ID; the rest stay
  const tbody = $('droneTable').querySelector('tbody');
  const ids = Object.keys(res.drones).map(Number);
  for (const tr of [...tbody.querySelectorAll('tr')]) {
    const v = rowValues(tr);
    if (!v.id && !v.addr) tr.remove();
    else if (ids.includes(Number(v.id))) tr.remove();
  }
  for (const id of ids.sort((a, b) => a - b)) addDroneRow(id, res.drones[id]);
  $('dNote').textContent = `Imported ${ids.length} drone(s) from ${file.name}. Check them, then Save.`;
}

/* ---------------------------------------------------------------- projects */

let projects = [];

async function loadProjects() {
  const res = await (await fetch('/api/projects')).json();
  projects = res.projects;
  const box = $('projects');
  box.innerHTML = '';
  box.disabled = !projects.length;
  if (!projects.length) {
    box.append(new Option('no projects', ''));
    showProjectInfo(null);
    $('projectInfo').textContent = 'put a webGUI export folder in lighthouse_runner/projects/ and press Refresh';
    return;
  }
  for (const p of projects) {
    const n = p.robots.length;
    box.append(new Option(`${p.name} (${n} drone${n === 1 ? '' : 's'})`, p.name));
  }
  const saved = localStorageGet('lh.project');
  const want = [project?.name, saved, projects[0].name].find((name) => projects.some((p) => p.name === name));
  box.value = want;
  if (want !== project?.name) await selectProject(want);
  else showProjectInfo(want);
}

function showProjectInfo(name) {
  const p = projects.find((x) => x.name === name);
  const el = $('projectInfo');
  el.className = 'muted';
  if (!p) { el.textContent = ''; return; }
  if (p.error) { el.className = 'err'; el.textContent = p.error; return; }
  const g = Object.keys(p.groups).length;
  const when = p.modified ? new Date(p.modified * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '';
  el.textContent = `${g} group${g === 1 ? '' : 's'} · ${when}`;
}

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }

async function selectProject(name) {
  const res = await fetch(`/api/project?name=${encodeURIComponent(name)}`);
  if (!res.ok) { log(`couldn't open ${name}`, 'err'); return; }
  project = await res.json();
  try { localStorage.setItem('lh.project', name); } catch {}
  $('projects').value = name;
  showProjectInfo(name);
  sim = null;
  simError = null;
  simT = 0;
  playing = false;
  follow = null;
  buildRobots();
  updateSim();
  renderTabs();
  renderControls();
  if (!userMoved) setView('iso');
  // Draw the planned paths straight away; Simulate plays them
  if (idsValid()) simulate(false);
}

let tab = null;
function renderTabs() {
  const box = $('tabs');
  box.innerHTML = '';
  const files = Object.keys(project?.code || {});
  if (!files.includes(tab)) tab = files[0] || null;
  for (const f of files) {
    const b = document.createElement('button');
    b.textContent = f;
    b.className = f === tab ? 'sel' : '';
    b.addEventListener('click', () => { tab = f; renderTabs(); });
    box.append(b);
  }
  editor.setValue(tab ? project.code[tab] : '');
  // Jump to the blocks
  const at = editor.getValue().split('\n').findIndex((l) => l.includes('Insert Execution Code Here') || l.includes('Insert Nodes Here'));
  editor.refresh();
  editor.scrollTo(null, at >= 0 ? editor.heightAtLine(Math.max(0, at - 1), 'local') : 0);
}

/* ----------------------------------------------------------------- console */

const consoleEl = $('console');
// cflib noise that says nothing useful here: log blocks left on a drone by an earlier connection
const LOG_NOISE = /^Error no LogEntry to handle id=/;
function log(text, cls = '') {
  if (LOG_NOISE.test(text)) return;
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

async function post(url, body) {
  const res = await fetch(url, { method: 'POST', body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) log(data.error || `${url} failed`, 'err');
  return data;
}

/* ------------------------------------------------------------------ events */

function connectEvents() {
  const es = new EventSource('/events');
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === 'snapshot') {
      session = m.session;
      hubChecks = m.checks || {};
      hubStatus = m.status || {};
      if (Object.keys(m.geos || {}).length) { geos = m.geos; buildBaseStations(); }
      m.logs.forEach((l) => log(l.text, l.level === 'err' ? 'err' : ''));
      handlePhase(m.phase);
      for (const ev of m.poses) handlePoses(ev);
      flushTrails();
    } else {
      m.events.forEach(handle);
    }
  };
  es.onerror = () => {
    $('cSession').querySelector('.dot').className = 'dot err';
    $('sessionText').textContent = 'dashboard server offline';
  };
}

/* -------------------------------------------------------------- readout */

function renderReadout() {
  const el = $('readout');
  const rows = [];
  for (const r of robots) {
    const live = r.live.group.visible ? r.livePose : null;
    const p = live || (r.ghost.group.visible ? r.simPose?.p : null);
    if (!p) continue;
    const led = !live && r.simPose?.led;
    rows.push(`<div><i class="sw" style="background:${r.color}"></i><span>cf${r.id}${live ? '' : ' sim'}</span>` +
      `<span>${p.map((v) => v.toFixed(2).padStart(5, ' ')).join(' ')}</span>` +
      (led ? `<i class="led" style="background:rgb(${led.join(',')})"></i>` : '') + '</div>');
  }
  const html = rows.join('');
  if (el.innerHTML !== html) el.innerHTML = html;
}

/* -------------------------------------------------------------- layers */

function layer(name) {
  return document.querySelector(`[data-layer="${name}"]`).checked;
}
function applyLayers() {
  planGroup.visible = layer('plan');
  trailGroup.visible = layer('trail');
  fenceGroup.visible = layer('fence');
  for (const o of Object.values(bsObjs)) o.fov.visible = layer('fov');
  document.body.classList.toggle('nolabels', !layer('labels'));
  updateSim();
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

/* ------------------------------------------------------------------ wiring */

$('bRefresh').addEventListener('click', loadProjects);
$('bDrones').addEventListener('click', openDrones);
$('dAdd').addEventListener('click', () => addDroneRow());
$('dCancel').addEventListener('click', () => { $('dronesDialog').hidden = true; });
$('dSave').addEventListener('click', saveDrones);
$('dImport').addEventListener('change', importCrazyflies);
$('projects').addEventListener('change', (e) => { if (e.target.value) selectProject(e.target.value); });
$('bSim').addEventListener('click', () => (sim?.key === simKey() ? togglePlay() : simulate(true)));
$('bPlay').addEventListener('click', () => togglePlay());
$('scrub').addEventListener('input', (e) => { follow = null; playing = false; simT = Number(e.target.value); updateSim(); });
$('bCheck').addEventListener('click', check);
function showCode(on) {
  $('code').hidden = !on;
  $('bCode').textContent = `${on ? '▾' : '▸'} Code`;
  $('bCode').setAttribute('aria-expanded', String(on));
  if (on) renderTabs();
  try { localStorage.setItem('lh.code', on ? '1' : ''); } catch {}
}
$('bCode').addEventListener('click', () => showCode($('code').hidden));
$('bClearLog').addEventListener('click', () => { consoleEl.innerHTML = ''; });
showCode(localStorageGet('lh.code') === '1');
$('bFly').addEventListener('click', openFly);
$('flyCancel').addEventListener('click', closeFly);
$('flyGo').addEventListener('click', async () => { closeFly(); await post('/api/fly'); });
$('bStop').addEventListener('click', () => post('/api/stop'));
$('bClear').addEventListener('click', clearTrails);
$('bLayers').addEventListener('click', (e) => { e.stopPropagation(); $('layers').hidden = !$('layers').hidden; });
$('layers').addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => { $('layers').hidden = true; });
$('scale').addEventListener('input', (e) => setScale(Number(e.target.value)));
document.querySelectorAll('[data-layer]').forEach((el) => el.addEventListener('change', applyLayers));
document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('flyDialog').hidden) closeFly();
  if (e.key === 'Escape' && !$('dronesDialog').hidden) $('dronesDialog').hidden = true;
  if (e.key === ' ' && sim && !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(document.activeElement.tagName)
      && !document.activeElement.closest('.CodeMirror')) { e.preventDefault(); togglePlay(); }
});

/* ------------------------------------------------------------------ layout */

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  renderer.setSize(w, h);
  labelRenderer.setSize(w, h);
  resizeTrails();
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(host);

const divider = $('divider');
divider.addEventListener('pointerdown', (e) => {
  divider.setPointerCapture(e.pointerId);
  divider.classList.add('drag');
  const move = (ev) => {
    const pct = Math.min(75, Math.max(25, (ev.clientX / window.innerWidth) * 100));
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

const hdivider = $('hdivider');
try { const h = localStorage.getItem('lh.console'); if (h) document.documentElement.style.setProperty('--console', h); } catch {}
hdivider.addEventListener('pointerdown', (e) => {
  hdivider.setPointerCapture(e.pointerId);
  hdivider.classList.add('drag');
  const left = $('left').getBoundingClientRect();
  let h = null;
  const move = (ev) => {
    h = `${Math.round(Math.min(left.height * 0.6, Math.max(40, left.bottom - ev.clientY)))}px`;
    document.documentElement.style.setProperty('--console', h);
    editor.refresh();
  };
  const up = () => {
    hdivider.classList.remove('drag');
    hdivider.removeEventListener('pointermove', move);
    hdivider.removeEventListener('pointerup', up);
    try { if (h) localStorage.setItem('lh.console', h); } catch {}
  };
  hdivider.addEventListener('pointermove', move);
  hdivider.addEventListener('pointerup', up);
});

/* -------------------------------------------------------------------- loop */

let lastTrailFlush = 0;
function animate(now) {
  requestAnimationFrame(animate);
  if (camAnim) {
    const u = Math.min(1, (performance.now() - camAnim.t0) / 600);
    const k = 1 - Math.pow(1 - u, 3);
    controls.target.lerpVectors(camAnim.fromT, camAnim.target, k);
    camera.position.lerpVectors(camAnim.fromP, camAnim.position, k);
    if (u >= 1) camAnim = null;
  }
  controls.update();
  stepSim(now);
  if (now - lastTrailFlush > 200) { lastTrailFlush = now; flushTrails(); }
  updateRays();
  renderReadout();

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
}

/* -------------------------------------------------------------------- boot */

(async () => {
  fleet = await (await fetch('/api/fleet')).json();
  $('fleetName').textContent = fleet.file.split('/').slice(-2).join('/');
  renderFleetIds();
  buildFence();
  if (Object.keys(fleet.geos).length) { geos = fleet.geos; buildBaseStations(); }
  resize();
  setView('iso');
  camAnim && Object.assign(camAnim, { t0: performance.now() - 600 });
  await loadProjects();
  renderControls();
  connectEvents();
  requestAnimationFrame(animate);
})();

// Console hook for testing without hardware, e.g. __lh.handle({type: 'poses', drones: {cf1: {p: [0, 0, 0.5], a: [0, 0, 0]}}})
window.__lh = { handle };
