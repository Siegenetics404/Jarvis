import * as THREE from "three";

// ---------- numbers to play with ----------
const IOR = 1.45; // how strongly the glass bends light (water 1.33, glass 1.5)
const DISPERSION = 0.05; // how far the rainbow edges spread
const SWIRLS = 3; // glass ribbons wrapped around the orb
const DROPLETS = 6; // small drops floating around it
const BUFFER = 440; // drawing size in CSS pixels. The page scales it up or down.

// How each state looks. color is red, green, blue (0-255).
export const STATES = {
  idle: { color: [45, 125, 155], wobble: 0.045, speed: 0.35, spin: 0.25 },
  listening: { color: [0, 229, 255], wobble: 0.07, speed: 0.7, spin: 0.55 },
  thinking: { color: [255, 179, 0], wobble: 0.06, speed: 1.5, spin: 1.6 },
  speaking: { color: [124, 255, 178], wobble: 0.055, speed: 0.9, spin: 0.8 },
  ignored: { color: [176, 84, 96], wobble: 0.025, speed: 0.3, spin: 0.15 },
  error: { color: [255, 72, 72], wobble: 0.09, speed: 1.8, spin: 1.2 },
  alarm: { color: [255, 140, 0], wobble: 0.11, speed: 2.2, spin: 2.0 },
};

// A color theme only changes the calm states. Thinking, ignored, error and alarm keep their meaning.
export const THEMES = {
  arc: {
    idle: [45, 125, 155],
    listening: [0, 229, 255],
    speaking: [124, 255, 178],
  },
  emerald: {
    idle: [40, 140, 95],
    listening: [60, 235, 140],
    speaking: [190, 255, 120],
  },
  violet: {
    idle: [120, 95, 185],
    listening: [190, 140, 255],
    speaking: [255, 150, 230],
  },
  silver: {
    idle: [125, 135, 145],
    listening: [235, 240, 245],
    speaking: [255, 255, 255],
  },
};

// ---------- shader pieces ----------
// A made-up studio with soft boxes, strip lights and a colored kicker. The glass reflects and refracts this.
const ENV = /* glsl */ `
uniform vec3 uColor;
uniform float uTime;
uniform float uLevel;

vec3 env(vec3 d) {
  d = normalize(d);
  float a = uTime * 0.12;
  d = vec3(cos(a) * d.x + sin(a) * d.z, d.y, -sin(a) * d.x + cos(a) * d.z);
  float up = d.y * 0.5 + 0.5;
  vec3 col = mix(vec3(0.012, 0.016, 0.026), vec3(0.20, 0.27, 0.36), smoothstep(0.05, 1.0, up));
  col += vec3(1.00, 0.98, 0.95) * 3.0 * pow(max(dot(d, normalize(vec3(-0.55, 0.65, 0.52))), 0.0), 28.0);
  col += vec3(0.85, 0.93, 1.00) * 2.4 * smoothstep(0.955, 0.985, dot(d, normalize(vec3(0.72, 0.18, 0.67))));
  col += uColor * (1.6 + 1.2 * uLevel) * pow(max(dot(d, normalize(vec3(0.55, -0.55, 0.45))), 0.0), 5.0);
  col += vec3(1.0, 0.42, 0.18) * 0.55 * pow(max(dot(d, normalize(vec3(-0.7, -0.35, -0.6))), 0.0), 7.0);
  col += vec3(0.25, 0.5, 1.0) * 0.5 * pow(max(dot(d, normalize(vec3(0.2, 0.9, -0.4))), 0.0), 9.0);
  return col;
}`;

// Smooth 3D noise, used to make the surface wobble like water
const NOISE = /* glsl */ `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
            i.z + vec4(0.0, i1.z, i2.z, 1.0))
          + i.y + vec4(0.0, i1.y, i2.y, 1.0))
          + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}`;

const ORB_VERT = /* glsl */ `
uniform float uTime;
uniform float uWobble;
uniform float uSpeed;
uniform float uLevel;
varying vec3 vP;
varying vec3 vN;
${NOISE}

vec3 displace(vec3 p) {
  float n = snoise(p * 1.4 + vec3(0.0, uTime * uSpeed, uTime * uSpeed * 0.7));
  float n2 = snoise(p * 3.2 - vec3(uTime * uSpeed * 1.3));
  float amp = uWobble * (1.0 + 2.5 * uLevel);
  return p * (1.0 + amp * (n + 0.35 * n2));
}

void main() {
  vec3 p = normalize(position);
  vec3 up = abs(p.y) > 0.99 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
  vec3 t1 = normalize(cross(p, up));
  vec3 t2 = normalize(cross(p, t1));
  float e = 0.02;
  vec3 a = displace(p);
  vec3 b = displace(normalize(p + t1 * e));
  vec3 c = displace(normalize(p + t2 * e));
  vec3 n = normalize(cross(b - a, c - a));
  if (dot(n, a) < 0.0) n = -n;
  vP = a;
  vN = n;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(a, 1.0);
}`;

const ORB_FRAG = /* glsl */ `
uniform float uIor;
uniform float uDispersion;
varying vec3 vP;
varying vec3 vN;
${ENV}

void main() {
  vec3 N = normalize(vN);
  vec3 I = normalize(vP - cameraPosition);
  float ndv = clamp(dot(N, -I), 0.0, 1.0);
  float fres = pow(1.0 - ndv, 3.0);

  vec3 refl = env(reflect(I, N));

  // Light that goes through the ball. Each color bends a little differently: that is the rainbow.
  vec3 Pn = normalize(vP);
  vec3 trans = vec3(0.0);
  for (int c = 0; c < 3; c++) {
    float ior = uIor + (float(c) - 1.0) * uDispersion;
    vec3 T = refract(I, N, 1.0 / ior);
    vec3 Q = Pn + T * (-2.0 * dot(Pn, T));        // where the light leaves the far side
    vec3 n2 = -normalize(Q);
    vec3 O = refract(T, n2, ior);
    if (dot(O, O) < 1e-4) O = reflect(T, n2);      // trapped inside: bounce
    vec3 e = env(O);
    trans[c] = e[c];
  }

  vec3 tint = mix(vec3(1.0), uColor * 1.4, 0.35);
  vec3 col = trans * tint;
  col += uColor * (0.10 + 0.30 * uLevel) * pow(ndv, 1.5);     // a glow in the middle that shows the state
  col = mix(col, refl, fres);

  col = 1.0 - exp(-col * 1.4);                                // soft tone mapping
  col = pow(col, vec3(1.0 / 2.2));
  float alpha = clamp(max(max(col.r, col.g), col.b) + 0.22 + 0.5 * fres, 0.0, 1.0);
  alpha = max(alpha, max(max(col.r, col.g), col.b));
  gl_FragColor = vec4(col, alpha);                            // premultiplied
}`;

const RIBBON_VERT = /* glsl */ `
varying vec3 vWP;
varying vec3 vNW;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWP = wp.xyz;
  vNW = normalize(mat3(modelMatrix) * normal);
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const RIBBON_FRAG = /* glsl */ `
uniform float uHalf;     // -1 = only the half behind the orb, 1 = only the half in front, 0 = all
varying vec3 vWP;
varying vec3 vNW;
${ENV}

void main() {
  if (uHalf < -0.5 && vWP.z > 0.0) discard;
  if (uHalf > 0.5 && vWP.z <= 0.0) discard;
  vec3 N = normalize(vNW);
  vec3 I = normalize(vWP - cameraPosition);
  if (dot(N, -I) < 0.0) N = -N;
  float ndv = clamp(dot(N, -I), 0.0, 1.0);
  float fres = pow(1.0 - ndv, 2.5);
  vec3 R = reflect(I, N);
  vec3 col = vec3(0.0);
  for (int c = 0; c < 3; c++) {
    vec3 Rc = normalize(R + N * (float(c) - 1.0) * 0.22);      // each color reflects a bit differently
    vec3 e = env(Rc);
    col[c] = e[c];
  }
  col = col * (0.55 + 1.2 * fres) + uColor * 0.08;
  col = 1.0 - exp(-col * 1.4);
  col = pow(col, vec3(1.0 / 2.2));
  float alpha = clamp(max(max(col.r, col.g), col.b) * 0.85 + 0.12 + 0.55 * fres, 0.0, 1.0);
  alpha = max(alpha, max(max(col.r, col.g), col.b));
  gl_FragColor = vec4(col, alpha);
}`;

class Swirl extends THREE.Curve {
  constructor(turns, radius, phase) {
    super();
    this.turns = turns;
    this.radius = radius;
    this.phase = phase;
  }
  getPoint(t, target = new THREE.Vector3()) {
    const y = (t - 0.5) * 1.84;
    const r = this.radius * Math.sqrt(Math.max(0.02, 1.0 - y * y)) + 0.04;
    const a = t * this.turns * Math.PI * 2 + this.phase;
    return target.set(r * Math.cos(a), y * this.radius, r * Math.sin(a));
  }
}

// ---------- the orb ----------
export function createOrb(canvas) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    premultipliedAlpha: true,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.setSize(BUFFER, BUFFER, false); // false: the page decides how big it looks
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 50);
  camera.position.set(0, 0, 7.3);

  // Values every material shares, so changing one changes them all
  const shared = {
    uTime: { value: 0 },
    uColor: { value: new THREE.Color(0.18, 0.49, 0.6) },
    uLevel: { value: 0 },
  };

  const orbMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: shared.uTime,
      uColor: shared.uColor,
      uLevel: shared.uLevel,
      uWobble: { value: 0.05 },
      uSpeed: { value: 0.4 },
      uIor: { value: IOR },
      uDispersion: { value: DISPERSION },
    },
    vertexShader: ORB_VERT,
    fragmentShader: ORB_FRAG,
    transparent: true,
    premultipliedAlpha: true,
    depthWrite: false,
    depthTest: false,
  });
  const orb = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 48), orbMat);
  orb.renderOrder = 1;
  orb.frustumCulled = false;
  scene.add(orb);

  function ribbonMaterial(half) {
    return new THREE.ShaderMaterial({
      uniforms: {
        uTime: shared.uTime,
        uColor: shared.uColor,
        uLevel: shared.uLevel,
        uHalf: { value: half },
      },
      vertexShader: RIBBON_VERT,
      fragmentShader: RIBBON_FRAG,
      transparent: true,
      premultipliedAlpha: true,
      depthWrite: false,
      depthTest: false,
    });
  }
  const backMat = ribbonMaterial(-1); // drawn before the orb
  const frontMat = ribbonMaterial(1); // drawn after it
  const dropMat = ribbonMaterial(0);

  const swirls = [];
  for (let i = 0; i < SWIRLS; i++) {
    const curve = new Swirl(1.6 + i * 0.7, 1.12 + i * 0.07, i * 2.1);
    const geo = new THREE.TubeGeometry(curve, 240, 0.03 + i * 0.006, 10, false);
    const group = new THREE.Group();
    const back = new THREE.Mesh(geo, backMat);
    const front = new THREE.Mesh(geo, frontMat);
    back.renderOrder = 0;
    front.renderOrder = 2;
    back.frustumCulled = front.frustumCulled = false;
    group.add(back, front);
    group.rotation.z = (i - 1) * 0.55;
    group.rotation.x = 0.25 * i;
    scene.add(group);
    swirls.push({ group, speed: 0.6 + i * 0.35, dir: i % 2 ? -1 : 1 });
  }

  const dropGeo = new THREE.SphereGeometry(1, 20, 14);
  const droplets = [];
  for (let i = 0; i < DROPLETS; i++) {
    const mesh = new THREE.Mesh(dropGeo, dropMat);
    mesh.scale.setScalar(0.04 + Math.random() * 0.06);
    mesh.frustumCulled = false;
    scene.add(mesh);
    droplets.push({
      mesh,
      radius: 1.32 + Math.random() * 0.34,
      angle: Math.random() * Math.PI * 2,
      speed: (0.15 + Math.random() * 0.3) * (Math.random() < 0.5 ? -1 : 1),
      tilt: (Math.random() - 0.5) * 1.2,
      bob: Math.random() * 6,
    });
  }

  // ---------- state and animation ----------
  let state = "idle";
  let theme = "arc";
  let levelTarget = 0;
  let minDelta = 0; // 0 = as fast as the screen allows
  let lastDraw = 0;
  const cur = { wobble: 0.045, speed: 0.35, spin: 0.25, level: 0 };
  const goalColor = new THREE.Color();
  const clock = new THREE.Clock();

  function colorOf(name) {
    return (
      (THEMES[theme] && THEMES[theme][name]) ||
      (STATES[name] || STATES.idle).color
    );
  }

  function tick() {
    const now = performance.now();
    if (minDelta && now - lastDraw < minDelta) return;
    lastDraw = now;

    const dt = Math.min(clock.getDelta(), 0.1);
    const t = clock.elapsedTime;
    const goal = STATES[state];
    const k = 1 - Math.exp(-dt * 4);

    const rgb = colorOf(state);
    goalColor.setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255);
    shared.uColor.value.lerp(goalColor, k);
    cur.wobble += (goal.wobble - cur.wobble) * k;
    cur.speed += (goal.speed - cur.speed) * k;
    cur.spin += (goal.spin - cur.spin) * k;
    cur.level += (levelTarget - cur.level) * (1 - Math.exp(-dt * 12));

    shared.uTime.value = t;
    shared.uLevel.value = cur.level;
    orbMat.uniforms.uWobble.value = cur.wobble;
    orbMat.uniforms.uSpeed.value = cur.speed;

    for (const s of swirls) {
      s.group.rotation.y += dt * cur.spin * s.speed * s.dir;
      s.group.scale.setScalar(1 + cur.level * 0.05);
    }
    for (const d of droplets) {
      d.angle += dt * d.speed * (0.6 + cur.spin);
      const bob = Math.sin(t * 1.3 + d.bob) * 0.12;
      const x = Math.cos(d.angle) * d.radius;
      const z = Math.sin(d.angle) * d.radius;
      d.mesh.position.set(x, bob + z * d.tilt * 0.35, z);
      d.mesh.renderOrder = d.mesh.position.z < 0 ? 0 : 2;
    }

    renderer.render(scene, camera);
  }
  renderer.setAnimationLoop(tick);

  return {
    setState(name) {
      if (STATES[name]) state = name;
    },
    setTheme(name) {
      if (THEMES[name]) theme = name;
    },
    setLevel(value) {
      levelTarget = Math.max(0, Math.min(1, value));
    },
    setFps(fps) {
      minDelta = fps > 0 ? 1000 / fps : 0;
    },
    colorOf,
  };
}
