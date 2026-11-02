import * as THREE from "../vendor/three/build/three.module.js?v=1";

// Buildings of the Blender city that open a dashboard section. Names are the
// glTF node names written by blender/build_city.py.
export const SECTIONS = [
  {
    page: "Modelo", title: "Modelo", subtitle: "Predicción de demanda eléctrica",
    color: "#ffd23f", match: (name) => name.startsWith("substation_"),
  },
  {
    page: "Precios", title: "Precios", subtitle: "Previsión del precio de la electricidad",
    color: "#21d18b", match: (name) => name === "core_sw_tower_0",
  },
  {
    page: "Datos", title: "Datos", subtitle: "Fuentes, conexión y cobertura",
    color: "#3fd3d9", match: (name) => name === "core_ne_tower_3",
  },
  {
    page: "Estado del sistema", title: "Estado del sistema", subtitle: "API, modelos y servicios",
    color: "#b59cff", match: (name) => name === "mid_e_s_building-g_00",
  },
];

// Inline SVG (never a text glyph, which some devices render as an emoji).
const CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';

function gradientTexture(radial) {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  const gradient = radial
    ? context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
    : context.createLinearGradient(0, size, 0, 0);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(radial ? 0.55 : 0.35, "rgba(255,255,255,.35)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function auraMaterial(color, map, opacity, side = THREE.DoubleSide) {
  return new THREE.MeshBasicMaterial({
    color, map, transparent: true, opacity, depthWrite: false, side,
    blending: THREE.AdditiveBlending, toneMapped: false,
  });
}

/**
 * Marks the section buildings, adds a coloured aura around each one and a
 * labelled button above it. Must run before repeated meshes are instanced.
 */
export function createHotspots({ root, scene, camera, canvas, layer, onOpen }) {
  root.updateMatrixWorld(true);
  const wallMap = gradientTexture(false);
  const groundMap = gradientTexture(true);
  const hotspots = [];

  for (const section of SECTIONS) {
    const targets = [];
    root.traverse((node) => {
      if (section.match(node.name) && !targets.some((target) => isDescendant(node, target))) {
        targets.push(node);
      }
    });
    if (!targets.length) continue;
    const meshes = [];
    const bounds = new THREE.Box3();
    for (const target of targets) {
      bounds.expandByObject(target);
      target.traverse((node) => {
        if (!node.isMesh) return;
        node.userData.hotspot = section.page;
        meshes.push(node);
      });
    }
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const color = new THREE.Color(section.color);
    const aura = new THREE.Group();
    aura.name = `aura_${section.page}`;
    const building = targets.length === 1;

    // Glowing curtain fading upwards around the footprint.
    const pad = building ? 0.45 : 0.25;
    const wallHeight = building ? Math.min(size.y * 0.55, 5) : 2.2;
    const walls = new THREE.Mesh(
      new THREE.CylinderGeometry(Math.SQRT1_2, Math.SQRT1_2, 1, 4, 1, true),
      auraMaterial(color, wallMap, 0.5),
    );
    walls.rotation.y = Math.PI / 4;
    walls.scale.set(size.x + pad, wallHeight, size.z + pad);
    walls.position.set(center.x, bounds.min.y + wallHeight / 2, center.z);
    // Soft light pool on the ground.
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      auraMaterial(color, groundMap, 0.6));
    ground.rotation.x = -Math.PI / 2;
    ground.scale.set((size.x + pad) * 1.9, (size.z + pad) * 1.9, 1);
    ground.position.set(center.x, bounds.min.y + 0.06, center.z);
    aura.add(walls, ground);
    // Halo shell hugging the building silhouette.
    const shells = [];
    if (building) {
      const shellMaterial = auraMaterial(color, null, 0.22, THREE.BackSide);
      for (const mesh of meshes) {
        const shell = new THREE.Mesh(mesh.geometry, shellMaterial);
        shell.matrixAutoUpdate = false;
        const scale = new THREE.Matrix4().makeTranslation(center.x, bounds.min.y, center.z)
          .multiply(new THREE.Matrix4().makeScale(1.07, 1.025, 1.07))
          .multiply(new THREE.Matrix4().makeTranslation(-center.x, -bounds.min.y, -center.z));
        shell.matrix.copy(scale.multiply(mesh.matrixWorld));
        aura.add(shell);
        shells.push(shell);
      }
    }
    aura.traverse((node) => { if (node.isMesh) node.renderOrder = 3; });
    scene.add(aura);

    const label = document.createElement("button");
    label.type = "button";
    label.className = "hotspot";
    label.style.setProperty("--c", section.color);
    label.setAttribute("aria-label", `Abrir ${section.title}: ${section.subtitle}`);
    label.innerHTML = `<span class="hs-body"><span class="hs-dot"></span><span class="hs-text">`
      + `<strong>${section.title}</strong><small>${section.subtitle}</small></span>`
      + `<span class="hs-go">${CHEVRON}</span></span>`;
    label.addEventListener("click", () => onOpen(section));
    label.addEventListener("pointerenter", () => setHover(section.page));
    label.addEventListener("pointerleave", () => setHover(null));
    layer.appendChild(label);

    hotspots.push({
      section, meshes, aura, walls, ground, shells, label,
      anchor: new THREE.Vector3(center.x, bounds.max.y + 0.6, center.z),
      pickables: [...meshes, walls],
    });
  }

  let hovered = null;
  function setHover(page) {
    hovered = page;
    for (const hotspot of hotspots) {
      hotspot.label.classList.toggle("active", hotspot.section.page === page);
    }
    canvas.style.cursor = page ? "pointer" : "";
  }

  // Click on the building itself (drag = orbit, so only short clicks count).
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const pickables = hotspots.flatMap((hotspot) => hotspot.pickables);
  const pick = (event) => {
    const rect = canvas.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    return hit ? hotspots.find((hotspot) => hotspot.pickables.includes(hit.object)) : null;
  };
  let downAt = null;
  const onMove = (event) => {
    if (event.buttons) return;
    setHover(pick(event)?.section.page ?? null);
  };
  const onDown = (event) => { downAt = { x: event.clientX, y: event.clientY }; };
  const onUp = (event) => {
    if (!downAt || Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y) > 6) return;
    const hotspot = pick(event);
    if (hotspot) onOpen(hotspot.section);
  };
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointerup", onUp);

  const projected = new THREE.Vector3();
  function update(seconds) {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const placed = [];
    hotspots.forEach((hotspot, index) => {
      const active = hovered === hotspot.section.page;
      const pulse = 0.5 + 0.5 * Math.sin(seconds * 2.2 + index * 1.3);
      hotspot.walls.material.opacity = (active ? 0.85 : 0.38) + pulse * 0.18;
      hotspot.ground.material.opacity = (active ? 0.95 : 0.5) + pulse * 0.2;
      for (const shell of hotspot.shells) shell.material.opacity = (active ? 0.4 : 0.16) + pulse * 0.1;
      projected.copy(hotspot.anchor).project(camera);
      const visible = projected.z < 1 && Math.abs(projected.x) < 1.05 && Math.abs(projected.y) < 1.05;
      hotspot.label.style.display = visible ? "" : "none";
      if (visible) {
        placed.push({
          hotspot, x: (projected.x * 0.5 + 0.5) * width, y: (-projected.y * 0.5 + 0.5) * height,
        });
      }
    });
    // Declutter: labels nearer the bottom of the frame keep their place, the
    // others are lifted (with a longer stem) until no two labels overlap.
    placed.sort((a, b) => b.y - a.y);
    const boxes = [];
    for (const item of placed) {
      const body = item.hotspot.label.firstElementChild;
      const w = body.offsetWidth;
      const h = body.offsetHeight;
      let lift = 0;
      for (let guard = 0; guard < 12; guard += 1) {
        const top = item.y - 12 - lift - h;
        const box = { left: item.x - w / 2 - 4, right: item.x + w / 2 + 4, top: top - 4,
          bottom: item.y - 12 - lift + 4 };
        const clash = boxes.find((other) => box.left < other.right && box.right > other.left
          && box.top < other.bottom && box.bottom > other.top);
        if (!clash) {
          boxes.push(box);
          break;
        }
        lift += box.bottom - clash.top + 2;
      }
      item.hotspot.label.style.setProperty("--lift", `${lift.toFixed(0)}px`);
      item.hotspot.label.style.transform = `translate(${item.x.toFixed(1)}px, ${item.y.toFixed(1)}px)`;
    }
  }

  function dispose() {
    canvas.removeEventListener("pointermove", onMove);
    canvas.removeEventListener("pointerdown", onDown);
    canvas.removeEventListener("pointerup", onUp);
    wallMap.dispose();
    groundMap.dispose();
    hotspots.forEach((hotspot) => hotspot.label.remove());
  }

  return { update, dispose, count: hotspots.length };
}

function isDescendant(node, ancestor) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (parent === ancestor) return true;
  }
  return false;
}
