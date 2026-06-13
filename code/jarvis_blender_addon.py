"""
JARVIS Blender Addon
====================
Installazione:
  Edit > Preferences > Add-ons > Install > seleziona questo file > attiva

Cosa fa:
  1. Apre un socket TCP su localhost:6789 — Jarvis manda codice Python, Blender lo esegue
  2. Ad ogni modifica della scena (manuale o via codice) rigenera blender_code.py
     nella cartella di Jarvis (~/<jarvis_dir>/blender_code.py)
  3. blender_code.py contiene bpy.ops.wm.save_as_mainfile + tutto lo stato
     della scena in Python puro, così Jarvis e Qwen sanno sempre a che punto si è
"""

bl_info = {
    "name":        "JARVIS Bridge",
    "author":      "Radostin / JARVIS v9",
    "version":     (1, 0, 0),
    "blender":     (4, 0, 0),
    "location":    "Properties > Scene > JARVIS Bridge",
    "description": "Socket bridge tra JARVIS e Blender + sync blender_code.py",
    "category":    "System",
}

import bpy
import socket
import threading
import traceback
import os
import time
from pathlib import Path

# ── Configurazione ────────────────────────────────────────────────────────────
SOCKET_HOST   = "localhost"
SOCKET_PORT   = 6789
# Cartella di Jarvis — stessa logica del loader .env in jarvis_v8.py
# Modifica questo path se la tua cartella è diversa
JARVIS_DIR    = Path.home() / "Documents" / "modelli"
BLENDER_CODE  = JARVIS_DIR / "blender_code.py"

# Throttle: rigenera blender_code.py al massimo ogni N secondi
# (evita riscritture continue mentre muovi un oggetto)
EXPORT_THROTTLE_SEC = 2.0

# ─────────────────────────────────────────────────────────────────────────────

_server_thread  = None
_server_socket  = None
_last_export_ts = 0.0
_export_pending = False


# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT SCENA → blender_code.py
# ══════════════════════════════════════════════════════════════════════════════

def _material_to_python(mat, var_name: str) -> list[str]:
    """Genera le righe Python per ricreare un materiale base."""
    lines = []
    lines.append(f'{var_name} = bpy.data.materials.new(name="{mat.name}")')
    lines.append(f'{var_name}.use_nodes = {mat.use_nodes}')
    if mat.use_nodes and mat.node_tree:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            base = bsdf.inputs["Base Color"].default_value
            r, g, b, a = round(base[0], 4), round(base[1], 4), round(base[2], 4), round(base[3], 4)
            lines.append(f'_bsdf = {var_name}.node_tree.nodes.get("Principled BSDF")')
            lines.append(f'if _bsdf: _bsdf.inputs["Base Color"].default_value = ({r}, {g}, {b}, {a})')
            roughness = round(bsdf.inputs["Roughness"].default_value, 4)
            metallic  = round(bsdf.inputs["Metallic"].default_value, 4)
            lines.append(f'if _bsdf: _bsdf.inputs["Roughness"].default_value = {roughness}')
            lines.append(f'if _bsdf: _bsdf.inputs["Metallic"].default_value = {metallic}')
    return lines


def export_scene_to_python() -> str:
    """
    Genera uno script Python che, eseguito in Blender, ricrea la scena corrente.
    Copre: mesh (con primitive riconosciute), luci, camera, materiali base.
    """
    lines = [
        "# blender_code.py — generato automaticamente da JARVIS Bridge",
        "# Rappresenta l'ultimo stato della scena Blender.",
        "# NON modificare manualmente: verrà sovrascritto ad ogni cambiamento.",
        "",
        "import bpy, math",
        "",
        "# ── Reset scena ──────────────────────────────────────────────────────",
        "bpy.ops.object.select_all(action='SELECT')",
        "bpy.ops.object.delete(use_global=False)",
        "",
    ]

    mat_vars: dict[str, str] = {}   # mat.name → nome variabile python
    mat_lines: list[str] = []
    mat_counter = 0

    obj_lines: list[str] = []

    for obj in bpy.data.objects:
        vname = f"obj_{obj.name.replace(' ', '_').replace('.', '_')}"
        loc   = tuple(round(v, 4) for v in obj.location)
        rot   = tuple(round(v, 4) for v in obj.rotation_euler)
        sca   = tuple(round(v, 4) for v in obj.scale)

        # ── Riconosci il tipo di oggetto ──────────────────────────────────────
        if obj.type == "MESH":
            mesh = obj.data
            # Prova a riconoscere primitive dal nome del mesh
            mname = mesh.name.lower()
            if "cube" in mname:
                obj_lines.append(f'bpy.ops.mesh.primitive_cube_add(location={loc})')
            elif "sphere" in mname or "uvsphere" in mname:
                obj_lines.append(f'bpy.ops.mesh.primitive_uv_sphere_add(location={loc})')
            elif "cylinder" in mname:
                obj_lines.append(f'bpy.ops.mesh.primitive_cylinder_add(location={loc})')
            elif "plane" in mname:
                obj_lines.append(f'bpy.ops.mesh.primitive_plane_add(location={loc})')
            elif "cone" in mname:
                obj_lines.append(f'bpy.ops.mesh.primitive_cone_add(location={loc})')
            elif "torus" in mname:
                obj_lines.append(f'bpy.ops.mesh.primitive_torus_add(location={loc})')
            else:
                # Mesh custom: crea da vertici/facce
                verts = [tuple(round(v, 4) for v in vert.co) for vert in mesh.vertices]
                faces = [tuple(p.vertices) for p in mesh.polygons]
                obj_lines.append(f'_mesh_{vname} = bpy.data.meshes.new("{mesh.name}")')
                obj_lines.append(f'_mesh_{vname}.from_pydata({verts}, [], {faces})')
                obj_lines.append(f'_mesh_{vname}.update()')
                obj_lines.append(f'{vname} = bpy.data.objects.new("{obj.name}", _mesh_{vname})')
                obj_lines.append(f'bpy.context.collection.objects.link({vname})')
                obj_lines.append(f'bpy.context.view_layer.objects.active = {vname}')
                obj_lines.append(f'{vname}.select_set(True)')
            obj_lines.append(f'{vname} = bpy.context.active_object')
            obj_lines.append(f'{vname}.name = "{obj.name}"')

        elif obj.type == "LIGHT":
            light = obj.data
            ltype = light.type  # POINT, SUN, SPOT, AREA
            energy = round(light.energy, 2)
            color  = tuple(round(v, 4) for v in light.color)
            obj_lines.append(f'_ldata_{vname} = bpy.data.lights.new(name="{light.name}", type="{ltype}")')
            obj_lines.append(f'_ldata_{vname}.energy = {energy}')
            obj_lines.append(f'_ldata_{vname}.color = {color}')
            obj_lines.append(f'{vname} = bpy.data.objects.new("{obj.name}", _ldata_{vname})')
            obj_lines.append(f'bpy.context.collection.objects.link({vname})')

        elif obj.type == "CAMERA":
            cam = obj.data
            lens = round(cam.lens, 2)
            obj_lines.append(f'_cdata_{vname} = bpy.data.cameras.new(name="{cam.name}")')
            obj_lines.append(f'_cdata_{vname}.lens = {lens}')
            obj_lines.append(f'{vname} = bpy.data.objects.new("{obj.name}", _cdata_{vname})')
            obj_lines.append(f'bpy.context.collection.objects.link({vname})')

        else:
            # Tipo non gestito — salta
            continue

        # ── Trasformazioni comuni ─────────────────────────────────────────────
        obj_lines.append(f'{vname}.location = {loc}')
        obj_lines.append(f'{vname}.rotation_euler = {rot}')
        obj_lines.append(f'{vname}.scale = {sca}')

        # ── Materiali ─────────────────────────────────────────────────────────
        if obj.type == "MESH" and obj.data.materials:
            for mat in obj.data.materials:
                if mat is None:
                    continue
                if mat.name not in mat_vars:
                    mat_counter += 1
                    mvar = f"mat_{mat_counter}"
                    mat_vars[mat.name] = mvar
                    mat_lines.extend(_material_to_python(mat, mvar))
                mvar = mat_vars[mat.name]
                obj_lines.append(f'if {vname}.data.materials: {vname}.data.materials[0] = {mvar}')
                obj_lines.append(f'else: {vname}.data.materials.append({mvar})')

        obj_lines.append("")  # riga vuota tra oggetti

    # ── Assembla il file ──────────────────────────────────────────────────────
    if mat_lines:
        lines.append("# ── Materiali ──────────────────────────────────────────────────────────")
        lines.extend(mat_lines)
        lines.append("")

    lines.append("# ── Oggetti ────────────────────────────────────────────────────────────")
    lines.extend(obj_lines)

    # ── Camera attiva ─────────────────────────────────────────────────────────
    if bpy.context.scene.camera:
        cam_name = bpy.context.scene.camera.name
        lines.append(f'# ── Camera attiva ─────────────────────────────────────────────────────')
        lines.append(f'_cam_obj = bpy.data.objects.get("{cam_name}")')
        lines.append(f'if _cam_obj: bpy.context.scene.camera = _cam_obj')
        lines.append("")

    return "\n".join(lines)


def write_blender_code():
    """Scrive blender_code.py nella cartella di Jarvis."""
    global _last_export_ts, _export_pending
    try:
        JARVIS_DIR.mkdir(parents=True, exist_ok=True)
        code = export_scene_to_python()
        BLENDER_CODE.write_text(code, encoding="utf-8")
        _last_export_ts = time.time()
        _export_pending = False
    except Exception:
        traceback.print_exc()


def _throttled_export():
    """Chiamato dall'handler — scrive solo se il throttle è scaduto."""
    global _export_pending, _last_export_ts
    now = time.time()
    if now - _last_export_ts >= EXPORT_THROTTLE_SEC:
        write_blender_code()
    else:
        _export_pending = True


# ══════════════════════════════════════════════════════════════════════════════
#  SOCKET SERVER — riceve codice Python da Jarvis
# ══════════════════════════════════════════════════════════════════════════════

def _execute_in_blender(code: str) -> str:
    """Esegue il codice nel contesto Blender (chiamato dal main thread via timer)."""
    try:
        exec(compile(code, "<jarvis>", "exec"), {"bpy": bpy})
        # Dopo esecuzione, aggiorna subito blender_code.py
        write_blender_code()
        return "OK"
    except Exception as e:
        return f"ERROR: {traceback.format_exc()}"


# Coda thread-safe: il thread socket mette qui il codice,
# il timer Blender (main thread) lo esegue
_code_queue: list[tuple] = []
_queue_lock = threading.Lock()


def _socket_worker():
    """Thread che ascolta connessioni TCP da Jarvis."""
    global _server_socket
    try:
        _server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _server_socket.bind((SOCKET_HOST, SOCKET_PORT))
        _server_socket.listen(5)
        _server_socket.settimeout(1.0)
        print(f"[JARVIS Bridge] Socket in ascolto su {SOCKET_HOST}:{SOCKET_PORT}")
        while True:
            try:
                conn, addr = _server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # socket chiuso, stop
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
    except Exception:
        traceback.print_exc()


def _handle_client(conn: socket.socket):
    """Riceve il codice, lo mette in coda per l'esecuzione nel main thread."""
    try:
        chunks = []
        while True:
            data = conn.recv(65536)
            if not data:
                break
            chunks.append(data)
        code = b"".join(chunks).decode("utf-8")
        if not code.strip():
            conn.sendall(b"ERROR: codice vuoto\n")
            return
        # Metti in coda con il conn per rispondere dopo
        result_event = threading.Event()
        result_holder = [None]
        with _queue_lock:
            _code_queue.append((code, result_holder, result_event))
        # Aspetta che il main thread esegua (max 30 sec)
        result_event.wait(timeout=30)
        response = (result_holder[0] or "ERROR: timeout").encode("utf-8") + b"\n"
        conn.sendall(response)
    except Exception:
        traceback.print_exc()
    finally:
        conn.close()


def _process_code_queue():
    """Timer Blender — eseguito nel main thread ogni 0.1 sec."""
    global _export_pending, _last_export_ts
    with _queue_lock:
        items = list(_code_queue)
        _code_queue.clear()
    for code, result_holder, event in items:
        result_holder[0] = _execute_in_blender(code)
        event.set()
    # Export pendente da handler?
    if _export_pending and (time.time() - _last_export_ts >= EXPORT_THROTTLE_SEC):
        write_blender_code()
    return 0.1  # richiama tra 0.1 sec


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLER MODIFICHE MANUALI
# ══════════════════════════════════════════════════════════════════════════════

@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    """Scatta ad ogni modifica della scena — aggiorna blender_code.py."""
    _throttled_export()


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL UI
# ══════════════════════════════════════════════════════════════════════════════

class JARVIS_PT_Panel(bpy.types.Panel):
    bl_label       = "JARVIS Bridge"
    bl_idname      = "JARVIS_PT_panel"
    bl_space_type  = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context     = "scene"

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Socket: {SOCKET_HOST}:{SOCKET_PORT}", icon="NETWORK_DRIVE")
        layout.label(text=f"blender_code.py:", icon="FILE_SCRIPT")
        layout.label(text=str(BLENDER_CODE))
        layout.operator("jarvis.export_now", text="Esporta ora", icon="EXPORT")


class JARVIS_OT_ExportNow(bpy.types.Operator):
    bl_idname  = "jarvis.export_now"
    bl_label   = "Esporta scena ora"
    bl_description = "Forza la rigenerazione immediata di blender_code.py"

    def execute(self, context):
        write_blender_code()
        self.report({"INFO"}, f"blender_code.py scritto in {BLENDER_CODE}")
        return {"FINISHED"}


# ══════════════════════════════════════════════════════════════════════════════
#  REGISTER / UNREGISTER
# ══════════════════════════════════════════════════════════════════════════════

_classes = [JARVIS_PT_Panel, JARVIS_OT_ExportNow]


def register():
    global _server_thread
    for cls in _classes:
        bpy.utils.register_class(cls)
    # Handler modifiche scena
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    # Timer main thread per coda codice
    if not bpy.app.timers.is_registered(_process_code_queue):
        bpy.app.timers.register(_process_code_queue, persistent=True)
    # Thread socket
    _server_thread = threading.Thread(target=_socket_worker, daemon=True)
    _server_thread.start()
    # Esporta subito lo stato iniziale
    write_blender_code()
    print("[JARVIS Bridge] Addon registrato.")


def unregister():
    global _server_socket
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if bpy.app.timers.is_registered(_process_code_queue):
        bpy.app.timers.unregister(_process_code_queue)
    if _server_socket:
        try:
            _server_socket.close()
        except Exception:
            pass
    print("[JARVIS Bridge] Addon rimosso.")


if __name__ == "__main__":
    register()
