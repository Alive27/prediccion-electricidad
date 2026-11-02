import * as THREE from "../vendor/three/build/three.module.js?v=1";
import { OrbitControls } from "../vendor/three/examples/jsm/controls/OrbitControls.js?v=1";
import { GLTFLoader } from "../vendor/three/examples/jsm/loaders/GLTFLoader.js?v=1";
import { createHotspots } from "./hotspots.js?v=3";

// The city is built in Blender (blender/build_city.py) and exported as one GLB.
// The sidecar JSON carries the energy routes used by the demand pulses.
const CITY_GLB = new URL("./energy_city.glb?v=5", import.meta.url).href;
const CITY_LAYOUT = new URL("./energy_city.json?v=1", import.meta.url).href;
const GROUND_DISTRICTS = new Set(["ROADS", "TERRAIN"]);

const canvas = document.querySelector("#city");
const errorBox = document.querySelector("#error");
const loading = document.querySelector("#loading");
const hotspotLayer = document.querySelector("#hotspots");
const config = window.ENERGY_CITY_CONFIG || { demandScale: 0, forecastScale: [] };

if (!canvas || !window.WebGLRenderingContext) {
  errorBox.style.display = "block";
} else {
  try {
    startScene();
  } catch (error) {
    console.error("No se pudo iniciar la ciudad energética:", error);
    errorBox.style.display = "block";
  }
}

function startScene() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#06120e");
  scene.fog = new THREE.Fog("#06120e", 95, 190);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 400);
  camera.position.set(60, 52, 64);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 0);
  controls.enableDamping = true;
  controls.enableZoom = false;
  controls.dampingFactor = 0.06;
  controls.rotateSpeed = 0.6;
  controls.minDistance = 40;
  controls.maxDistance = 110;
  controls.enablePan = false;
  // From a near top-down view to the default aerial angle; distance stays fixed.
  controls.minPolarAngle = Math.PI * 0.02;
  controls.maxPolarAngle = Math.PI * 0.42;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.35;
  let resumeTimer = 0;
  controls.addEventListener("start", () => {
    controls.autoRotate = false;
    clearTimeout(resumeTimer);
  });
  controls.addEventListener("end", () => {
    clearTimeout(resumeTimer);
    resumeTimer = setTimeout(() => { controls.autoRotate = true; }, 8000);
  });
  // Fixed zoom: the wheel never zooms the city, it scrolls the dashboard page.
  const onWheel = (event) => {
    event.preventDefault();
    window.parent.scrollBy({ top: event.deltaY, behavior: "auto" });
  };
  canvas.addEventListener("wheel", onWheel, { capture: true, passive: false });

  scene.add(new THREE.HemisphereLight(0xd7f5e6, 0x0b1a14, 1.25));
  const key = new THREE.DirectionalLight(0xfff1dc, 2.1);
  key.position.set(-28, 46, 22);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -40, right: 40, top: 40, bottom: -40, near: 1, far: 140 });
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.03;
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x7ff0d8, 0.45);
  fill.position.set(30, 18, -26);
  scene.add(fill);

  const city = new THREE.Group();
  scene.add(city);
  const glowMaterials = [];
  const emissiveLights = [];
  const pulses = [];
  let energyRoutes = [];
  let hotspots = null;

  const glow = new THREE.MeshBasicMaterial({
    color: 0x36f2c0, transparent: true, opacity: 0.4, depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const pulseGeo = new THREE.SphereGeometry(0.2, 12, 8);

  Promise.all([
    new GLTFLoader().loadAsync(CITY_GLB),
    fetch(CITY_LAYOUT).then((response) => {
      if (!response.ok) throw new Error(`energy_city.json HTTP ${response.status}`);
      return response.json();
    }),
  ]).then(([gltf, layout]) => {
    prepareCity(gltf.scene);
    city.add(gltf.scene);
    hotspots = createHotspots({
      root: gltf.scene, scene, camera, canvas, layer: hotspotLayer, onOpen: openSection,
    });
    const drawCalls = instanceRepeatedMeshes(gltf.scene);
    energyRoutes = Object.values(layout.energyRoutes || {}).filter((route) => route.length > 1);
    createPulses();
    createEnergyLights(gltf.scene);
    cityFrame = measureCity();
    resize();
    if (loading) loading.style.display = "none";
    canvas.dataset.cityReady = "true";
    canvas.dataset.drawGroups = String(drawCalls);
    canvas.dataset.hotspots = String(hotspots.count);
  }).catch((error) => {
    console.error("Falló la carga de la ciudad 3D:", error);
    if (loading) loading.style.display = "none";
    errorBox.textContent = "No se pudo cargar la ciudad 3D local. "
      + "Los indicadores siguen mostrando los datos reales de la API.";
    errorBox.style.display = "block";
  });

  function prepareCity(root) {
    root.traverse((node) => {
      if (!node.isMesh) return;
      const district = node.userData.district || node.parent?.userData?.district;
      const ground = GROUND_DISTRICTS.has(district) || /_ground|green_pockets/.test(node.name);
      node.castShadow = !ground;
      node.receiveShadow = true;
      for (const material of [].concat(node.material)) {
        if (material.name === "city_glow" && !glowMaterials.includes(material)) {
          material.userData.baseIntensity = material.emissiveIntensity || 1;
          glowMaterials.push(material);
        }
        if (material.map) material.map.anisotropy = 4;
      }
    });
  }

  function createPulses() {
    const count = Math.max(12, energyRoutes.length * 4);
    for (let i = 0; i < count; i += 1) {
      const pulse = new THREE.Mesh(pulseGeo, glow.clone());
      pulse.userData.phase = (i / count + (i % 3) * 0.17) % 1;
      pulse.userData.route = i % energyRoutes.length;
      pulse.position.y = 0.32;
      pulse.renderOrder = 2;
      scene.add(pulse);
      pulses.push(pulse);
    }
  }

  function createEnergyLights(root) {
    const anchors = ["plaza_energy_beacon", "substation_transformer_T2"]
      .map((name) => root.getObjectByName(name)).filter(Boolean);
    for (const anchor of anchors) {
      const position = anchor.getWorldPosition(new THREE.Vector3());
      const light = new THREE.PointLight(0x1bd68a, 1.2, 9, 2);
      light.position.set(position.x, position.y + 2.2, position.z);
      scene.add(light);
      emissiveLights.push(light);
    }
  }

  let cityFrame = null;
  function measureCity() {
    city.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(city);
    if (bounds.isEmpty()) return null;
    const center = bounds.getCenter(new THREE.Vector3());
    center.y = 0;
    const corners = [];
    for (const x of [bounds.min.x, bounds.max.x]) {
      for (const z of [bounds.min.z, bounds.max.z]) corners.push(new THREE.Vector3(x, 0, z));
    }
    return { center, corners };
  }

  // Fixed zoom: from the initial aerial three-quarter view (south-east), the
  // terrain spans the full width of the frame. While orbiting, corners may leave
  // the frame; the distance never changes.
  function fitCamera() {
    if (!cityFrame) return;
    const { center, corners } = cityFrame;
    const direction = new THREE.Vector3(1, 0.86, 1.05).normalize();
    const probe = camera.clone();
    const spans = (distance) => {
      probe.position.copy(center).addScaledVector(direction, distance);
      probe.lookAt(center);
      probe.updateMatrixWorld(true);
      return Math.max(...corners.map((corner) => Math.abs(corner.clone().project(probe).x)));
    };
    let low = 5;
    let high = 400;
    for (let i = 0; i < 32; i += 1) {
      const mid = (low + high) / 2;
      if (spans(mid) > 1) low = mid; else high = mid;
    }
    const distance = high;
    controls.target.copy(center);
    controls.minDistance = distance;
    controls.maxDistance = distance;
    const current = camera.position.clone().sub(center);
    const keepOrbit = current.lengthSq() > 1 && camera.userData.fitted;
    camera.position.copy(center).add(
      keepOrbit ? current.setLength(distance) : direction.clone().multiplyScalar(distance));
    camera.userData.fitted = true;
    camera.far = distance * 3;
    scene.fog.near = distance * 1.05;
    scene.fog.far = distance * 2.4;
    camera.updateProjectionMatrix();
    controls.update();
  }

  function resize() {
    const width = Math.max(1, canvas.clientWidth);
    const height = Math.max(1, canvas.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    // Leave the top band of the frame for the KPI cards: shift the view down.
    // On phones the cards take a taller band, so the city sits lower.
    const shift = width < 680 ? 0.2 : 0.08;
    camera.setViewOffset(width, height, 0, -height * shift, width, height);
    camera.updateProjectionMatrix();
    fitCamera();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  let frame = 0;
  function animate() {
    frame = requestAnimationFrame(animate);
    const seconds = performance.now() * 0.001;
    const forecast = config.forecastScale || [];
    const index = forecast.length ? Math.floor(seconds / 3) % forecast.length : 0;
    const forecastLevel = forecast[index] ?? config.demandScale ?? 0;
    const demandLevel = THREE.MathUtils.clamp(
      (config.demandScale ?? 0) * 0.65 + forecastLevel * 0.35, 0, 1,
    );
    const glowLevel = 0.18 + demandLevel * 0.6;
    pulses.forEach((pulse) => {
      const route = energyRoutes[pulse.userData.route];
      const distance = ((seconds * (0.035 + demandLevel * 0.06) + pulse.userData.phase) % 1);
      const segment = Math.min(route.length - 2, Math.floor(distance * (route.length - 1)));
      const t = distance * (route.length - 1) - segment;
      pulse.position.x = THREE.MathUtils.lerp(route[segment][0], route[segment + 1][0], t);
      pulse.position.z = THREE.MathUtils.lerp(route[segment][1], route[segment + 1][1], t);
      pulse.material.opacity = glowLevel;
    });
    emissiveLights.forEach((light, indexLight) => {
      light.intensity = 0.45 + demandLevel * 2.8
        + Math.sin(seconds * 1.25 + indexLight) * (0.08 + demandLevel * 0.12);
    });
    const glowPulse = 0.35 + demandLevel * 0.9 + Math.sin(seconds * 1.6) * 0.05;
    glowMaterials.forEach((material) => {
      material.emissiveIntensity = material.userData.baseIntensity * glowPulse;
    });
    controls.update();
    renderer.render(scene, camera);
    hotspots?.update(seconds);
  }
  animate();

  window.addEventListener("pagehide", () => {
    cancelAnimationFrame(frame);
    clearTimeout(resumeTimer);
    observer.disconnect();
    controls.dispose();
    hotspots?.dispose();
    canvas.removeEventListener("wheel", onWheel, true);
    disposeScene(scene);
    renderer.dispose();
  }, { once: true });
}

// Opens a dashboard section: clicks the matching sidebar button of the Streamlit
// page hosting this same-origin iframe; falls back to the ?page= URL parameter.
function openSection(section) {
  const key = (config.navigation || {})[section.page];
  try {
    const button = key && window.parent.document.querySelector(`.st-key-${key} button`);
    if (button) {
      button.click();
      return;
    }
    const url = new URL(window.parent.location.href);
    url.searchParams.set("page", section.page);
    window.parent.location.assign(url.href);
  } catch (error) {
    console.error("No se pudo abrir la sección desde la ciudad:", error);
  }
}

// Repeated Kenney models share one geometry/material pair in the GLB; draw each
// pair once with an InstancedMesh instead of one draw call per placed object.
function instanceRepeatedMeshes(root) {
  root.updateMatrixWorld(true);
  const groups = new Map();
  root.traverse((node) => {
    if (!node.isMesh || node.isInstancedMesh || Array.isArray(node.material)
      || node.userData.hotspot) return;
    const key = `${node.geometry.uuid}|${node.material.uuid}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  });
  const inverseRoot = root.matrixWorld.clone().invert();
  const instancedRoot = new THREE.Group();
  instancedRoot.name = "instanced_city";
  let drawGroups = 0;
  for (const meshes of groups.values()) {
    drawGroups += 1;
    if (meshes.length < 2) continue;
    const [first] = meshes;
    const instanced = new THREE.InstancedMesh(first.geometry, first.material, meshes.length);
    const matrix = new THREE.Matrix4();
    meshes.forEach((mesh, i) => {
      instanced.setMatrixAt(i, matrix.multiplyMatrices(inverseRoot, mesh.matrixWorld));
      mesh.removeFromParent();
    });
    instanced.instanceMatrix.needsUpdate = true;
    instanced.computeBoundingSphere();
    instanced.castShadow = first.castShadow;
    instanced.receiveShadow = first.receiveShadow;
    instanced.name = `instanced_${first.name}`;
    instancedRoot.add(instanced);
  }
  root.add(instancedRoot);
  return drawGroups;
}

function disposeScene(scene) {
  scene.traverse((node) => {
    if (!node.isMesh) return;
    node.geometry?.dispose();
    for (const material of [].concat(node.material)) {
      material.map?.dispose();
      material.dispose();
    }
  });
}
