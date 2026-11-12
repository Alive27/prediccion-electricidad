import * as THREE from "../vendor/three/build/three.module.js?v=1";
import { mergeGeometries } from "../vendor/three/examples/jsm/utils/BufferGeometryUtils.js?v=1";

// Amber trails travelling along the street grid cables built in Blender. energy_city.json
// carries a few routes: each is one continuous line of cable from the substation that
// takes a single way at every branch. One trail runs along each route, from the
// substation to the end of its line, then starts again. One merged mesh, one draw call.

const vertexShader = /* glsl */ `
  attribute float aDist;    // distance along the route from the substation
  attribute float aCycle;   // route length plus the pause before the next trail
  attribute float aPhase;   // offset so the routes do not start together
  varying float vDist;
  varying float vCycle;
  varying float vPhase;
  void main() {
    vDist = aDist;
    vCycle = aCycle;
    vPhase = aPhase;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  uniform float uOffset;     // distance travelled by every trail head (world units)
  uniform float uTrail;      // length of the fading tail
  uniform float uIntensity;  // brightness, driven by demand
  uniform vec3 uColor;
  varying float vDist;
  varying float vCycle;
  varying float vPhase;
  void main() {
    float head = mod(uOffset + vPhase, vCycle);
    float behind = head - vDist;
    if (behind < 0.0) discard;
    float alpha = smoothstep(0.0, 0.3, behind) * exp(-behind / uTrail) * uIntensity;
    if (alpha < 0.012) discard;
    gl_FragColor = vec4(uColor * alpha, alpha);
  }
`;

const PAUSE = 18;   // world units of cable "travelled" in the dark between two trails

export function createEnergyNetwork(routes, scene) {
  const geometries = [];
  (routes ?? []).forEach((route, index) => {
    if (!route.points || route.points.length < 2) return;
    const points = route.points.map(([x, y, z]) => new THREE.Vector3(x, y, z));
    const curve = new THREE.CatmullRomCurve3(points, false, "centripetal");
    const length = curve.getLength();
    const geometry = new THREE.TubeGeometry(curve, Math.max(8, Math.ceil(length / 0.3)),
      0.13, 5, false);
    const uv = geometry.getAttribute("uv");
    const count = uv.count;
    const cycle = length + PAUSE;
    const distances = new Float32Array(count);
    for (let i = 0; i < count; i += 1) distances[i] = uv.getX(i) * length;
    geometry.setAttribute("aDist", new THREE.BufferAttribute(distances, 1));
    geometry.setAttribute("aCycle", new THREE.BufferAttribute(new Float32Array(count).fill(cycle), 1));
    // Stagger the routes so trails leave the substation one after another.
    geometry.setAttribute("aPhase", new THREE.BufferAttribute(
      new Float32Array(count).fill(cycle * (index / Math.max(1, routes.length))), 1));
    geometry.deleteAttribute("normal");
    geometry.deleteAttribute("uv");
    geometries.push(geometry);
  });
  if (!geometries.length) return null;
  const merged = mergeGeometries(geometries);
  geometries.forEach((geometry) => geometry.dispose());
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uOffset: { value: 0 }, uTrail: { value: 4.5 }, uIntensity: { value: 0.9 },
      uColor: { value: new THREE.Color(0xffc24a) },
    },
    vertexShader, fragmentShader, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, toneMapped: false,
  });
  const mesh = new THREE.Mesh(merged, material);
  mesh.name = "energy_network_trails";
  mesh.frustumCulled = false;
  mesh.renderOrder = 2;
  scene.add(mesh);

  return {
    paths: geometries.length,
    // More demand: faster and brighter energy, as with the previous animation.
    update(delta, demandLevel) {
      material.uniforms.uOffset.value += delta * (3 + demandLevel * 6);
      material.uniforms.uIntensity.value = 0.6 + demandLevel * 1.1;
    },
    dispose() {
      merged.dispose();
      material.dispose();
    },
  };
}
