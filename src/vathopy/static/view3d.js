// SPDX-License-Identifier: GPL-3.0-or-later
// The GUI's 3D view: a small low-poly wireframe of the depth map's surface with the keep shapes, shape
// focuses, focus planes and cut slabs placed in it, so you can see where they sit in space. Drag to
// orbit, scroll to zoom, right-drag to pan, double-click to go back to the start view.
//
// Space is the engine's shape space (engine.py, focus_shape_weight): x and y in fractions of the
// picture's width (centred, y up), z = depth as a fraction of the window (0 far, 1 near) x the look's
// depth_scale. A plane focus lives in the add-on's relief space, where depth is stretched by the
// look's scale: its normal is converted with `zk` from /api/mesh_preview.
import * as THREE from "three";
import { OrbitControls } from "/static/OrbitControls.js";

const GRID_COLS = 48;
const KEEP_COLOUR = "#4fb3a9", CUT_COLOUR = "#e62828";

// A frame (x', y', z') as engine.shape_axes / engine.rod_axes.
function shapeAxes(lean, angle) {
  const le = THREE.MathUtils.degToRad(lean), a = THREE.MathUtils.degToRad(angle);
  const z = new THREE.Vector3(Math.sin(le) * Math.cos(a), Math.sin(le) * Math.sin(a), Math.cos(le));
  const x = new THREE.Vector3(Math.cos(le) * Math.cos(a), Math.cos(le) * Math.sin(a), -Math.sin(le));
  return [x, new THREE.Vector3().crossVectors(z, x), z];
}
function rodAxes(tilt, angle) {
  const t = THREE.MathUtils.degToRad(tilt), a = THREE.MathUtils.degToRad(angle);
  const z = new THREE.Vector3(-Math.sin(a) * Math.cos(t), Math.cos(a) * Math.cos(t), Math.sin(t));
  const x = new THREE.Vector3(Math.cos(a), Math.sin(a), 0);
  return [x, new THREE.Vector3().crossVectors(z, x), z];
}

// The wireframe of one shape in its own frame (axis z'), sizes as the engine reads them.
function shapeLines(b, colour) {
  const sx = Math.max(+b.size_x || 0.15, 1e-3), sy = Math.max(+b.size_y || 0.15, 1e-3), sz = Math.max(+b.size_z || 0.15, 1e-3);
  let geo;
  if (b.shape === "box") geo = new THREE.EdgesGeometry(new THREE.BoxGeometry(2 * sx, 2 * sy, 2 * sz));
  else if (b.shape === "cylinder") {
    const len = +b.length > 0 ? +b.length : 2.5;                   // endless: through the whole scene
    geo = new THREE.WireframeGeometry(new THREE.CylinderGeometry(sx, sx, len, 16, 1, true).rotateX(Math.PI / 2));
  } else if (b.shape === "cone") {
    const h = 0.5, r = h * Math.tan(THREE.MathUtils.degToRad(Math.min(Math.max(+b.cone_angle || 30, 0.5), 89.5)));
    const lathe = new THREE.LatheGeometry([new THREE.Vector2(r, -h), new THREE.Vector2(0, 0), new THREE.Vector2(r, h)], 16);
    geo = new THREE.WireframeGeometry(lathe.rotateX(Math.PI / 2));
  } else {                                                        // ellipsoid
    geo = new THREE.WireframeGeometry(new THREE.SphereGeometry(1, 14, 8).rotateX(Math.PI / 2).scale(sx, sy, sz));
  }
  return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: colour }));
}

// The grid turned/mirrored like the picture (np.rot90 counter-clockwise, then left-right).
function orientGrid(g, turns, mirror) {
  let { cols, rows, t } = g;
  for (let k = 0; k < turns; k++) {
    const out = new Array(t.length);
    for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) out[i * rows + j] = t[j * cols + (cols - 1 - i)];
    t = out; [cols, rows] = [rows, cols];
  }
  if (mirror) {
    const out = new Array(t.length);
    for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) out[i * cols + j] = t[i * cols + (cols - 1 - j)];
    t = out;
  }
  return { cols, rows, t };
}

// The picture wrapped round a cylinder or sphere of radius 1 (z up), as mesh.cylinder_mesh / sphere_mesh lay
// it out, near raised by relief/radius; the picture on screen as its texture.
function wrappedMesh(g, w, img) {
  const { cols, rows, t } = g, k = w.radius > 0 ? w.relief / w.radius : 0;
  const arc = THREE.MathUtils.degToRad(Math.min(Math.max(w.arc || 360, 1), 360));
  const lat = w.lat > 0 ? THREE.MathUtils.degToRad(Math.min(w.lat, 180)) : Math.min(arc * rows / cols, Math.PI);
  const step = arc / (cols - 1), pos = [], uv = [], idx = [];
  for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
    const h = t[i * cols + j], R = 1 + k * (w.invert ? 1 - h : h), th = step * j;
    if (w.shape === "sphere") {
      const ph = lat / 2 - lat * i / (rows - 1);
      pos.push(R * Math.cos(ph) * Math.cos(th), R * Math.cos(ph) * Math.sin(th), R * Math.sin(ph));
    } else pos.push(R * Math.cos(th), R * Math.sin(th), ((rows - 1) / 2 - i) * step);
    uv.push(j / (cols - 1), 1 - i / (rows - 1));
  }
  for (let i = 0; i + 1 < rows; i++) for (let j = 0; j + 1 < cols; j++) {
    const a = i * cols + j, b = a + 1, c = a + cols, d = c + 1;
    idx.push(a, c, b, b, c, d);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  geo.setIndex(idx);
  const group = new THREE.Group();
  let tex = null;
  if (img && img.complete && img.naturalWidth) {
    tex = new THREE.Texture(img);
    tex.colorSpace = THREE.SRGBColorSpace; tex.needsUpdate = true;
  }
  group.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, color: tex ? 0xffffff : 0x888888, side: THREE.DoubleSide })));
  group.add(new THREE.LineSegments(new THREE.WireframeGeometry(geo), new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.12 })));
  group.rotation.x = -Math.PI / 2;                                // z up -> the view's y up
  return group;
}

export function start(canvas, page) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 50);
  const controls = new OrbitControls(camera, canvas);
  // The picture's orientation: turned (inner) then mirrored (outer), as the picture on screen.
  const outer = new THREE.Group(), inner = new THREE.Group();
  outer.add(inner); scene.add(outer);
  const surface = new THREE.Group(), things = new THREE.Group();
  inner.add(surface, things);
  const wrapped = new THREE.Group();
  scene.add(wrapped);
  let grid = null, gridKey = "", fetching = false, again = false, lastShape = "flat", lastShapeKey = "";

  const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
  function size() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * renderer.getPixelRatio()) || canvas.height !== Math.round(h * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h; camera.updateProjectionMatrix();
    }
  }
  function draw() { size(); renderer.render(scene, camera); }
  controls.addEventListener("change", draw);
  if (typeof ResizeObserver === "function") new ResizeObserver(() => draw()).observe(canvas);   // a detached, resized view

  function home() {
    if (lastShape !== "flat") {                                   // the wrapped picture, all in view
      const b = new THREE.Box3().setFromObject(wrapped);
      const sph = b.isEmpty() ? new THREE.Sphere(new THREE.Vector3(), 1.5) : b.getBoundingSphere(new THREE.Sphere());
      const dist = sph.radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05;
      controls.target.copy(sph.center);
      camera.up.set(0, 1, 0);
      camera.position.set(sph.center.x, sph.center.y + 0.25 * dist, sph.center.z + 0.97 * dist);
      controls.update();
      return;
    }
    const ds = page.depthScale();
    controls.target.set(0, 0, ds / 2);
    camera.up.set(0, 1, 0);
    camera.position.set(1.1, 0.75, ds / 2 + 2.3);               // in front, a little right and above
    controls.update();
  }
  canvas.addEventListener("dblclick", () => { home(); draw(); });

  // The surface: a GRID_COLS-wide grid of the windowed depth, drawn as quads (lines along rows and columns).
  function buildSurface() {
    surface.clear();
    if (!grid || !page.showSurface()) return;
    const { cols, rows, aspect, t } = grid, ds = page.depthScale();
    const at = (i, j) => [(j + 0.5) / cols - 0.5, (0.5 - (i + 0.5) / rows) * aspect, t[i * cols + j] * ds];
    const pts = [];
    for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
      if (j + 1 < cols) pts.push(...at(i, j), ...at(i, j + 1));
      if (i + 1 < rows) pts.push(...at(i, j), ...at(i + 1, j));
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    surface.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: css("--dim"), transparent: true, opacity: 0.55 })));
    // The picture's frame on the far plane, for scale.
    const w = 0.5, h = aspect / 2;
    const frame = new THREE.BufferGeometry().setFromPoints([[-w, -h], [w, -h], [w, h], [-w, h], [-w, -h]].map(([x, y]) => new THREE.Vector3(x, y, 0)));
    surface.add(new THREE.Line(frame, new THREE.LineBasicMaterial({ color: css("--rule") })));
  }

  // A flat quad (the picture's size) through `c` with normal `n`, edged, faintly filled.
  function slab(c, n, colour, opacity) {
    const aspect = grid ? grid.aspect : 1;
    const q = new THREE.PlaneGeometry(1, aspect);
    const m = new THREE.Mesh(q, new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity, side: THREE.DoubleSide, depthWrite: false }));
    const e = new THREE.LineSegments(new THREE.EdgesGeometry(q), new THREE.LineBasicMaterial({ color: colour }));
    const g = new THREE.Group();
    g.add(m, e);
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), n.clone().normalize());
    g.position.copy(c);
    return g;
  }

  function buildThings() {
    things.clear();
    const ds = page.depthScale(), aspect = grid ? grid.aspect : 1;
    const centre = (b) => new THREE.Vector3((b.x ?? 0.5) - 0.5, (0.5 - (b.y ?? 0.5)) * aspect, (+b.depth || 0) * ds);
    const place = (obj, b, rod) => {
      const [x, y, z] = (rod ? rodAxes : shapeAxes)(+b.lean || 0, +b.angle || 0);
      obj.matrixAutoUpdate = false;
      obj.matrix.makeBasis(x, y, z).setPosition(centre(b));
      return obj;
    };
    const s = page.settings();
    // The cut: what's farther than Delete from far, and nearer than Delete from near, is removed.
    if (s.cut_depth > 0 && s.cut_depth < 1) things.add(slab(new THREE.Vector3(0, 0, s.cut_depth * ds), new THREE.Vector3(0, 0, 1), CUT_COLOUR, 0.12));
    if (s.cut_near > 0 && s.cut_near < 1) things.add(slab(new THREE.Vector3(0, 0, (1 - s.cut_near) * ds), new THREE.Vector3(0, 0, 1), CUT_COLOUR, 0.12));
    page.keeps().forEach((k) => { if (k.enabled !== false) things.add(place(shapeLines(k, KEEP_COLOUR), k, k.shape === "cylinder")); });
    page.bands().forEach((b, i) => {
      if (b.enabled === false) return;
      const colour = page.focusColour(i);
      if ((b.shape || "plane") !== "plane") { things.add(place(shapeLines(b, colour), b, b.shape === "cylinder")); return; }
      // A plane focus: its band's plane, the add-on's normal converted from relief space to this one.
      const le = THREE.MathUtils.degToRad(+b.lean || 0), a = THREE.MathUtils.degToRad(+b.angle || 0);
      const zk = grid ? grid.zk / ds : 1;
      const n = new THREE.Vector3(Math.sin(le) * Math.cos(a), Math.sin(le) * Math.sin(a), Math.cos(le) * zk);
      things.add(slab(centre(b), n, colour, 0.18));
    });
  }

  // Lay the scene out again; a new map, smoothing or window fetches the surface first.
  async function update() {
    const map = page.map();
    if (!map) return;
    const s = page.settings(), key = JSON.stringify([map, s.smooth, s.stl_follow !== false ? s.reshape : null]);
    const turns = s.orientation?.turns || 0, mirror = !!s.orientation?.mirror;
    inner.rotation.z = turns * Math.PI / 2;
    outer.scale.x = mirror ? -1 : 1;
    if (key !== gridKey) {
      if (fetching) { again = true; return; }
      fetching = true;
      try {
        grid = await page.getJSON("/api/mesh_preview", { map, settings: s, cols: GRID_COLS });
        gridKey = key;
        if (!controls.userMoved) home();
      } catch (e) { grid = null; }
      finally { fetching = false; }
      if (again) { again = false; return update(); }
    }
    const w = page.wrap(), shapeKey = JSON.stringify([w.shape, w.arc, w.lat, w.radius > 0 ? w.relief / w.radius : 0, turns]);
    wrapped.traverse((o) => { o.geometry?.dispose(); o.material?.map?.dispose(); o.material?.dispose(); });
    wrapped.clear();
    outer.visible = w.shape === "flat";
    if (w.shape !== "flat" && grid) {
      wrapped.add(wrappedMesh(orientGrid(grid, turns, mirror), w, page.picture()));
    } else { buildSurface(); buildThings(); }
    lastShape = w.shape;
    if (shapeKey !== lastShapeKey) { lastShapeKey = shapeKey; controls.userMoved = false; home(); }   // a new shape: frame it
    draw();
  }
  controls.addEventListener("start", () => { controls.userMoved = true; });
  new ResizeObserver(draw).observe(canvas);
  home();
  return { update, home: () => { controls.userMoved = false; home(); draw(); } };
}
