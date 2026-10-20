"""QuadWrap - Semi-Automatic Retopology for Maya (week-1 skeleton)

Design doc: QUADWRAP_DESIGN.md
Status: week-1 scaffolding. Implements settings persistence, selection
validation, guide-strip creation from vertex selections, registry, UI, and
basic stats. Shrink-wrap fitting is wired in week 2; heat map / deform
test in week 3.

Habits (inherited from chandelier_generator.py and class feedback):
  - _require_maya guard so the file imports cleanly outside Maya.
  - Sanitize every input; never crash on bad selection.
  - Delete only what we created (registry + group), never wildcards.
  - One undo chunk per mutating action.
"""
import json
import math
import os
import traceback

try:
    import maya.cmds as cmds
except Exception:  # pragma: no cover - allows import outside Maya
    cmds = None

try:
    import maya.OpenMaya as om
except Exception:
    om = None

try:
    import maya.api.OpenMaya as om2
except Exception:
    om2 = None


# ---------------------------------------------------------------------------
# Constants / registry
# ---------------------------------------------------------------------------
TOOL_NAME = "QuadWrap"
PREFIX = "qw_"
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".quadwrap")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
# Bump when a stored setting changes meaning, so load_settings can migrate it.
SETTINGS_VERSION = 2


CREATED_GUIDES = []      # transform names we created (guide strips / curves)
GUIDE_META = {}          # name -> dict metadata
COMMITTED = []           # committed cage transform names
ACTION_STACK = []        # names for "Undo Last": type-prefixed e.g. "guide:xxx"

_CURRENT = {
    "target": None,      # transform
    "target_shape": None,
    "cage": None,
    "cage_shape": None,
    "ghost": None,
    "sw_node": None,
}

_COUNTERS = {"guide": 0, "cage": 0, "ghost": 0, "committed": 0}


_DEFAULT_SETTINGS = {
    "projection_strength": 1.0,
    "density": 1.0,
    "symmetry_blend": 1.0,
    "vertex_target": 5000,
    "snap_offset": 0.0,
    "preset": "Prop",
    "projection_mode": 4,   # 4 = closest
    "bidirectional": 1,
    "closest_if_no_intersection": 1,
    "target_smooth_level": 0,
    "mirror_axis": "X",
}

PRESETS = {
    "Prop": {
        "projection_strength": 1.0,
        "density": 1.0,
        "symmetry_blend": 1.0,
        "vertex_target": 3000,
        "snap_offset": 0.0,
        "projection_mode": 4,
    },
    "Character": {
        "projection_strength": 0.85,
        "density": 1.4,
        "symmetry_blend": 1.0,
        "vertex_target": 8000,
        "snap_offset": 0.001,
        "projection_mode": 2,   # vertex normals-ish intent
    },
    "Hard-Surface": {
        "projection_strength": 0.6,
        "density": 0.9,
        "symmetry_blend": 1.0,
        "vertex_target": 4000,
        "snap_offset": 0.0,
        "projection_mode": 1,   # parallel-to-axes intent
    },
}

_SETTINGS = dict(_DEFAULT_SETTINGS)


# ---------------------------------------------------------------------------
# Maya guards
# ---------------------------------------------------------------------------
def _require_maya():
    if cmds is None:
        raise RuntimeError("QuadWrap must run inside Maya (maya.cmds not found).")


def _unique(prefix):
    _COUNTERS[prefix] = _COUNTERS.get(prefix, 0) + 1
    return "{}{}_{}".format(PREFIX, prefix, _COUNTERS[prefix])


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------
def _clamp_float(value, default, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = default
    if v != v:  # NaN guard
        v = default
    return max(lo, min(hi, v))


def _clamp_int(value, default, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _sanitize_settings(s):
    out = dict(_DEFAULT_SETTINGS)
    out["projection_strength"] = _clamp_float(
        s.get("projection_strength", out["projection_strength"]), 1.0, 0.0, 1.0)
    out["density"] = _clamp_float(
        s.get("density", out["density"]), 1.0, 0.1, 5.0)
    out["symmetry_blend"] = _clamp_float(
        s.get("symmetry_blend", out["symmetry_blend"]), 0.0, 0.0, 1.0)
    out["vertex_target"] = _clamp_int(
        s.get("vertex_target", out["vertex_target"]), 5000, 100, 200000)
    out["snap_offset"] = _clamp_float(
        s.get("snap_offset", out["snap_offset"]), 0.0, -0.1, 0.1)
    # NOTE: Maya's shrinkWrap ".projection" enum has exactly 5 entries
    # (0 Toward Inner, 1 Toward Center, 2 Parallel To Axes,
    #  3 Vertex Normals, 4 Closest). "Closest if no hit" is NOT a mode -
    # it is the separate ".closestIfNoIntersection" boolean.
    out["projection_mode"] = _clamp_int(
        s.get("projection_mode", out["projection_mode"]), 4, 0, 4)
    out["bidirectional"] = 1 if s.get("bidirectional", 1) else 0
    out["closest_if_no_intersection"] = 1 if s.get(
        "closest_if_no_intersection", 1) else 0
    out["target_smooth_level"] = _clamp_int(
        s.get("target_smooth_level", out["target_smooth_level"]), 0, 0, 4)
    axis = str(s.get("mirror_axis", out["mirror_axis"])).upper()
    out["mirror_axis"] = axis if axis in ("X", "Y", "Z") else "X"
    preset = s.get("preset", out["preset"])
    out["preset"] = preset if preset in list(PRESETS.keys()) + ["Custom"] else "Prop"
    return out


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
def load_settings():
    global _SETTINGS
    try:
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = dict(_DEFAULT_SETTINGS)
                if "settings" in data and isinstance(data["settings"], dict):
                    merged.update(data["settings"])
                if "custom_presets" in data:
                    # keep in file only; merged into a module-level holder below
                    _CUSTOM_PRESETS.update(data.get("custom_presets", {}))
                # Migration: Symmetry Blend used to be a no-op at both ends, so
                # a stored 0 meant "full mirror". It now means "no mirror",
                # so an old 0 has to become 1.
                if int(data.get("settings_version", 1)) < 2:
                    if not merged.get("symmetry_blend"):
                        merged["symmetry_blend"] = 1.0
                _SETTINGS.update(_sanitize_settings(merged))
                if int(data.get("settings_version", 1)) < 2:
                    save_settings()
    except Exception as exc:  # noqa: BLE001
        print("QuadWrap: could not load settings ({}), using defaults.".format(exc))


_CUSTOM_PRESETS = {}


def save_settings():
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        payload = {
            "settings": _SETTINGS,
            "custom_presets": _CUSTOM_PRESETS,
            "settings_version": SETTINGS_VERSION,
        }
        with open(SETTINGS_FILE, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        if cmds is not None:
            cmds.warning("QuadWrap: could not save settings: {}".format(exc))
        else:
            print("QuadWrap: could not save settings: {}".format(exc))


def apply_preset(name):
    global _SETTINGS
    if name in PRESETS:
        merged = dict(_SETTINGS)
        merged.update(PRESETS[name])
        merged["preset"] = name
        _SETTINGS = _sanitize_settings(merged)
        save_settings()
        return True
    if name in _CUSTOM_PRESETS:
        merged = dict(_SETTINGS)
        merged.update(_CUSTOM_PRESETS[name])
        merged["preset"] = name
        _SETTINGS = _sanitize_settings(merged)
        save_settings()
        return True
    return False


def save_current_as_preset(name):
    name = (name or "").strip()
    if not name:
        return False
    _CUSTOM_PRESETS[name] = {k: _SETTINGS[k] for k in _SETTINGS}
    _SETTINGS["preset"] = name
    save_settings()
    return True


# hydrate on import
load_settings()


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------
def _resolve_transform(node):
    """Given a selected node (transform or shape), return transform name."""
    if node is None:
        return None
    if not cmds.objExists(node):
        return None
    if cmds.nodeType(node) == "transform":
        shapes = cmds.listRelatives(node, shapes=True) or []
        if any(cmds.nodeType(s) == "mesh" for s in shapes):
            return node
        return node
    # shape selected
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    if parents:
        return parents[0]
    return None


def _shape_of(transform):
    if not transform or not cmds.objExists(transform):
        return None
    if cmds.nodeType(transform) == "mesh":
        return transform
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    for s in shapes:
        if cmds.nodeType(s) == "mesh" and not cmds.getAttr(s + ".intermediateObject"):
            return s
    return shapes[0] if shapes else None


def get_target_mesh():
    """Validate + store the high-poly target from the current selection."""
    _require_maya()
    sel = cmds.ls(selection=True) or []
    if len(sel) != 1:
        cmds.warning("QuadWrap: select exactly one mesh as the target.")
        return None, None
    tr = _resolve_transform(sel[0])
    shape = _shape_of(tr)
    if shape is None:
        cmds.warning("QuadWrap: selection is not a polygon mesh.")
        return None, None
    _CURRENT["target"] = tr
    _CURRENT["target_shape"] = shape
    return tr, shape


# ---------------------------------------------------------------------------
# Guide strip management
# ---------------------------------------------------------------------------
def create_strip_from_selection():
    """Turn an ordered vertex selection into a guide curve (strip)."""
    _require_maya()
    verts = [v for v in (cmds.ls(selection=True, flatten=True) or [])
             if ".vtx[" in str(v)]
    if len(verts) < 2:
        cmds.warning("QuadWrap: select 2+ vertices on the mesh in order.")
        return None
    # mesh must match
    mesh = verts[0].split(".")[0]
    positions = []
    for v in verts:
        try:
            positions.append(cmds.xform(v, query=True, worldSpace=True, translation=True))
        except Exception:  # noqa: BLE001
            cmds.warning("QuadWrap: could not read vertex position.")
            return None
    name = _unique("guide")
    try:
        cmds.curve(point=positions, degree=1, name=name)
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: curve creation failed: {}".format(exc))
        return None
    # Give the shape a unique name. Maya reuses "curveShape1" for every
    # duplicated curve, which makes any later query on it ambiguous.
    try:
        shapes = cmds.listRelatives(name, shapes=True, fullPath=True) or []
        if shapes:
            cmds.rename(shapes[0], name + "Shape")
    except Exception:  # noqa: BLE001
        pass
    CREATED_GUIDES.append(name)
    GUIDE_META[name] = {
        "source_verts": list(verts),
        "source_mesh": mesh,
        "closed": False,
    }
    ACTION_STACK.append("guide:" + name)
    try:
        cmds.select(name, replace=True)
    except Exception:  # noqa: BLE001
        pass
    return name


def close_loop(guide=None):
    _require_maya()
    if guide is None:
        sel = cmds.ls(selection=True) or []
        guide = sel[0] if sel else None
    if guide not in CREATED_GUIDES:
        cmds.warning("QuadWrap: selected guide is not one of ours.")
        return False
    try:
        cmds.closeCurve(guide, constructionHistory=False, preserveShape=True,
                        replaceOriginal=True)
        GUIDE_META[guide]["closed"] = True
        return True
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: close curve failed: {}".format(exc))
        return False


def auto_ring(guide=None, count=None, axis=None):
    _require_maya()
    if guide is None:
        sel = cmds.ls(selection=True) or []
        guide = sel[0] if sel else None
    if guide not in CREATED_GUIDES:
        cmds.warning("QuadWrap: select one of our guide strips to ring.")
        return 0
    count = count if count is not None else int(
        max(3, round(_SETTINGS["density"] * 4)))   # same rule as the UI button
    count = max(2, min(24, int(count)))
    if axis is None:
        axis = _SETTINGS["mirror_axis"]
    pos = cmds.xform(guide, query=True, worldSpace=True, translation=True)
    # original yaw (only Y matters for radial ring)
    try:
        rot = cmds.xform(guide, query=True, worldSpace=True, rotation=True)
        base_yaw = rot[1] if len(rot) > 1 else 0.0
    except Exception:  # noqa: BLE001
        base_yaw = 0.0
    made = 0
    for k in range(1, count):
        ang = 2.0 * math.pi * k / float(count)
        if axis.upper() == "X":
            ny = pos[1] * math.cos(ang) - pos[2] * math.sin(ang)
            nz = pos[1] * math.sin(ang) + pos[2] * math.cos(ang)
            npos = (pos[0], ny, nz)
            nyaw = base_yaw + math.degrees(ang)
        elif axis.upper() == "Z":
            nx = pos[0] * math.cos(ang) - pos[1] * math.sin(ang)
            ny = pos[0] * math.sin(ang) + pos[1] * math.cos(ang)
            npos = (nx, ny, pos[2])
            nyaw = base_yaw
        else:  # Y (default)
            nx = pos[0] * math.cos(ang) + pos[2] * math.sin(ang)
            nz = -pos[0] * math.sin(ang) + pos[2] * math.cos(ang)
            npos = (nx, pos[1], nz)
            nyaw = base_yaw + math.degrees(ang)
        try:
            dup = cmds.duplicate(guide)[0]
            cmds.xform(dup, worldSpace=True, translation=npos)
            if axis.upper() in ("X", "Y"):
                cmds.xform(dup, worldSpace=True, rotation=(0 if axis.upper() == "Y"
                                                            else nyaw, nyaw if axis.upper() == "Y" else 0, 0))
            CREATED_GUIDES.append(dup)
            GUIDE_META[dup] = dict(GUIDE_META.get(guide, {}))
            GUIDE_META[dup]["ring_parent"] = guide
            made += 1
            ACTION_STACK.append("guide:" + dup)
        except Exception as exc:  # noqa: BLE001
            cmds.warning("QuadWrap: ring duplicate failed: {}".format(exc))
    return made


def _guide_points(guide):
    """World-space control point positions of a guide strip, as a list of
    (x, y, z) tuples. Returns None if it cannot be read."""
    try:
        shape = cmds.listRelatives(guide, shapes=True, fullPath=True)[0]
        # control points = spans + degree for a nurbs curve
        spans = int(cmds.getAttr(shape + ".spans"))
        degree = int(cmds.getAttr(shape + ".degree"))
        count = spans + degree
    except Exception:  # noqa: BLE001
        return None
    if count < 1:
        return None
    pts = []
    for i in range(count):
        try:
            p = cmds.xform(shape + ".cv[{}]".format(i), query=True,
                           worldSpace=True, translation=True)
        except Exception:  # noqa: BLE001
            return None
        if not p or len(p) < 3:
            return None
        pts.append((p[0], p[1], p[2]))
    return pts


def mirror_guide(guide=None, axis=None, blend=None):
    _require_maya()
    if guide is None:
        sel = cmds.ls(selection=True) or []
        guide = sel[0] if sel else None
    if guide not in CREATED_GUIDES:
        cmds.warning("QuadWrap: select one of our guide strips to mirror.")
        return None
    if axis is None:
        axis = _SETTINGS["mirror_axis"]
    ax = axis.upper()

    # How hard to mirror, 0 = leave the strip alone, 1 = full mirror.
    if blend is None:
        blend = _SETTINGS["symmetry_blend"]
    try:
        blend = float(blend)
    except (TypeError, ValueError):
        blend = 1.0
    blend = max(0.0, min(1.0, blend))

    # Build the mirrored strip from the control points directly. Scaling a
    # duplicated transform is unreliable here: the curve's pivot is not at the
    # origin, so the result is not a clean reflection.
    src = _guide_points(guide)
    if not src:
        cmds.warning("QuadWrap: could not read the guide's points.")
        return None
    mirrored = []
    for (x, y, z) in src:
        if ax == "X":
            m = (-x, y, z)
        elif ax == "Y":
            m = (x, -y, z)
        else:
            m = (x, y, -z)
        # blend straight from the original to the mirrored point
        mirrored.append((x + (m[0] - x) * blend,
                         y + (m[1] - y) * blend,
                         z + (m[2] - z) * blend))

    name = _unique("guide")
    try:
        dup = cmds.curve(point=mirrored, degree=1, name=name)
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: mirror curve creation failed: {}".format(exc))
        return None
    try:
        shapes = cmds.listRelatives(dup, shapes=True, fullPath=True) or []
        if shapes:
            cmds.rename(shapes[0], name + "Shape")
    except Exception:  # noqa: BLE001
        pass
    CREATED_GUIDES.append(dup)
    GUIDE_META[dup] = dict(GUIDE_META.get(guide, {}))
    GUIDE_META[dup]["mirrored"] = True
    GUIDE_META[dup]["mirror_axis"] = ax
    GUIDE_META[dup]["mirror_blend"] = blend
    ACTION_STACK.append("guide:" + dup)
    return dup


def _safe_delete_node(name):
    if not name or not cmds.objExists(name):
        return False
    try:
        cmds.delete(name)
        return True
    except Exception:  # noqa: BLE001
        return False


# Nodes the deform test creates. Kept in one place so cleanup is never missed.
DEFORM_NODES = ("qw_test_skin", "qw_test_joint_b", "qw_test_joint_a")


def _clear_deform_rig():
    """Remove the deform-test skinCluster and joints if they exist.

    The skinCluster goes first so the mesh stops being weighted before the
    joints disappear.
    """
    removed = False
    for n in DEFORM_NODES:
        if cmds.objExists(n):
            if _safe_delete_node(n):
                removed = True
    return removed


def _prune_registry():
    """Drop tracked nodes that no longer exist.

    Maya's own undo can remove a node we still have on our list (undoing a
    commit, for example). Pruning keeps the stats and the heat map from
    pointing at meshes that are gone.
    """
    for name in list(CREATED_GUIDES):
        if not cmds.objExists(name):
            CREATED_GUIDES.remove(name)
            GUIDE_META.pop(name, None)
    for name in list(COMMITTED):
        if not cmds.objExists(name):
            COMMITTED.remove(name)


def undo_last():
    _require_maya()
    _prune_registry()
    # pop guides first
    while ACTION_STACK:
        token = ACTION_STACK.pop()
        kind, name = (token.split(":", 1) + [""])[:2]
        if kind == "guide":
            if name in CREATED_GUIDES:
                CREATED_GUIDES.remove(name)
            GUIDE_META.pop(name, None)
            _safe_delete_node(name)
            return True
        if kind == "committed":
            if name in COMMITTED:
                COMMITTED.remove(name)
            _safe_delete_node(name)
            return True
        if kind == "fit":
            detach_fit()
            return True
        if kind == "deform":
            # remove the test rig (joints + skin) instead of the mesh
            if _clear_deform_rig():
                return True
            continue
    # fallback: last committed
    if COMMITTED:
        name = COMMITTED.pop()
        _safe_delete_node(name)
        return True
    cmds.warning("QuadWrap: nothing to undo.")
    return False


def clear_all():
    _require_maya()
    cmds.undoInfo(openChunk=True)
    try:
        # the deform-test rig first: its joints are not children of the
        # committed mesh, so they would otherwise be left behind
        _clear_deform_rig()
        for name in list(CREATED_GUIDES):
            _safe_delete_node(name)
        CREATED_GUIDES[:] = []
        GUIDE_META.clear()
        for name in list(COMMITTED):
            _safe_delete_node(name)
        COMMITTED[:] = []
        ACTION_STACK[:] = []
        # remove live fit on cage
        if _CURRENT.get("sw_node") and cmds.objExists(_CURRENT["sw_node"]):
            try:
                cmds.delete(_CURRENT["sw_node"])
            except Exception:  # noqa: BLE001
                pass
        if _CURRENT.get("ghost") and cmds.objExists(_CURRENT["ghost"]):
            _safe_delete_node(_CURRENT["ghost"])
        _CURRENT["cage"] = None
        _CURRENT["cage_shape"] = None
        _CURRENT["ghost"] = None
        _CURRENT["sw_node"] = None
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------------------------
# Fit (shrink wrap driver) - week 2
# ---------------------------------------------------------------------------
def fit_to_surface():
    _require_maya()
    target = _CURRENT.get("target")
    if not target or not cmds.objExists(target):
        target, shape = get_target_mesh()
        if target is None:
            cmds.warning("QuadWrap: load/select a target mesh first.")
            return None
    sel = cmds.ls(selection=True) or []
    cage = None
    if sel:
        cage = _resolve_transform(sel[0])
    if not cage or cage == target:
        cmds.warning("QuadWrap: select a different mesh to use as the cage.")
        return None
    cage_shape = _shape_of(cage)
    target_shape = _CURRENT.get("target_shape") or _shape_of(target)
    if cage_shape is None or target_shape is None:
        cmds.warning("QuadWrap: could not resolve shapes.")
        return None

    # remove any prior node we own on this cage
    prev = _CURRENT.get("sw_node")
    if prev and cmds.objExists(prev):
        try:
            cmds.delete(prev)
        except Exception:  # noqa: BLE001
            pass

    cmds.undoInfo(openChunk=True)
    try:
        node = cmds.deformer(cage_shape, type="shrinkWrap")[0]
        # connect target surface
        cmds.connectAttr(target_shape + ".worldMesh[0]",
                         node + ".targetGeom", force=True)
        # basic smooth-target attrs (safe if present)
        for src, dst in (("continuity", "continuity"), ("smoothUVs", "smoothUVs"),
                         ("keepBorder", "keepBorder"), ("keepHardEdge", "keepHardEdge"),
                         ("keepMapBorders", "keepMapBorders"),
                         ("propagateEdgeHardness", "propagateEdgeHardness")):
            try:
                cmds.connectAttr(target_shape + "." + src, node + "." + dst, force=True)
            except Exception:  # noqa: BLE001
                pass
        # apply settings
        _apply_settings_to_node(node)
        _CURRENT["cage"] = cage
        _CURRENT["cage_shape"] = cage_shape
        _CURRENT["sw_node"] = node
        ACTION_STACK.append("fit:" + node)
        return node
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: fit failed: {}".format(exc))
        traceback.print_exc()
        return None
    finally:
        cmds.undoInfo(closeChunk=True)


def _apply_settings_to_node(node):
    if not node or not cmds.objExists(node):
        return
    try:
        cmds.setAttr(node + ".envelope", float(_SETTINGS["projection_strength"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        cmds.setAttr(node + ".projection", int(_SETTINGS["projection_mode"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        cmds.setAttr(node + ".offset", float(_SETTINGS["snap_offset"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        cmds.setAttr(node + ".bidirectional", int(_SETTINGS["bidirectional"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        cmds.setAttr(node + ".closestIfNoIntersection",
                     int(_SETTINGS["closest_if_no_intersection"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        cmds.setAttr(node + ".targetSmoothLevel",
                     int(_SETTINGS["target_smooth_level"]))
    except Exception:  # noqa: BLE001
        pass


def detach_fit():
    node = _CURRENT.get("sw_node")
    if node and cmds.objExists(node):
        try:
            cmds.delete(node)
        except Exception:  # noqa: BLE001
            pass
    _CURRENT["sw_node"] = None


# ---------------------------------------------------------------------------
# Preview / commit (week 2 basic)
# ---------------------------------------------------------------------------
def preview():
    _require_maya()
    cage = _CURRENT.get("cage")
    if not cage or not cmds.objExists(cage):
        cmds.warning("QuadWrap: fit a cage first.")
        return None
    cmds.undoInfo(openChunk=True)
    try:
        if _CURRENT.get("ghost") and cmds.objExists(_CURRENT["ghost"]):
            _safe_delete_node(_CURRENT["ghost"])
        g = cmds.duplicate(cage)[0]
        gname = _unique("ghost")
        try:
            cmds.rename(g, gname)
        except Exception:  # noqa: BLE001
            gname = g
        # ghost shading: semi-transparent / wire+shaded
        try:
            shapes = cmds.listRelatives(gname, shapes=True, fullPath=True) or []
            for sh in shapes:
                cmds.setAttr(sh + ".overrideEnabled", 1)
                cmds.setAttr(sh + ".overrideShading", 0)
                cmds.setAttr(sh + ".overrideDisplayType", 2)  # reference
        except Exception:  # noqa: BLE001
            pass
        _CURRENT["ghost"] = gname
        return gname
    finally:
        cmds.undoInfo(closeChunk=True)


def commit():
    _require_maya()
    _prune_registry()
    cage = _CURRENT.get("cage")
    if not cage or not cmds.objExists(cage):
        cmds.warning("QuadWrap: fit a cage first.")
        return None
    cmds.undoInfo(openChunk=True)
    try:
        dup = cmds.duplicate(cage)[0]
        try:
            cmds.delete(dup, constructionHistory=True)
        except Exception:  # noqa: BLE001
            pass
        name = _unique("committed")
        try:
            cmds.rename(dup, name)
        except Exception:  # noqa: BLE001
            name = dup
        COMMITTED.append(name)
        ACTION_STACK.append("committed:" + name)
        # cleanup preview + fit node (bake already captured positions)
        if _CURRENT.get("ghost") and cmds.objExists(_CURRENT["ghost"]):
            _safe_delete_node(_CURRENT["ghost"])
            _CURRENT["ghost"] = None
        detach_fit()
        return name
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: commit failed: {}".format(exc))
        traceback.print_exc()
        return None
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def count_non_manifold(mesh):
    _require_maya()
    shape = _shape_of(mesh) if not str(mesh).endswith("Shape") else mesh
    if shape is None:
        shape = mesh
    if not cmds.objExists(shape):
        return 0
    try:
        res = cmds.polyInfo(shape, nonManifoldEdges=True) or []
        return len(res)
    except Exception:  # noqa: BLE001
        return 0


def get_stats(mesh=None):
    _require_maya()
    _prune_registry()
    if mesh is None:
        mesh = _CURRENT.get("cage") or _CURRENT.get("target")
    if not mesh or not cmds.objExists(mesh):
        return {"faces": 0, "verts": 0, "guides": len(CREATED_GUIDES),
                "nonmanifold": 0}
    shape = _shape_of(mesh)
    try:
        faces = int(cmds.polyEvaluate(shape, face=True))
    except Exception:  # noqa: BLE001
        faces = 0
    try:
        verts = int(cmds.polyEvaluate(shape, vertex=True))
    except Exception:  # noqa: BLE001
        verts = 0
    return {
        "faces": faces,
        "verts": verts,
        "guides": len(CREATED_GUIDES),
        "nonmanifold": count_non_manifold(shape),
    }


# ---------------------------------------------------------------------------
# Heat map + deform test (week 3 placeholders)
# ---------------------------------------------------------------------------
def _resolve_heat_inputs(mesh, target, clamp):
    _require_maya()
    _prune_registry()
    if mesh is None:
        if COMMITTED:
            mesh = COMMITTED[-1]
        elif _CURRENT.get("cage"):
            mesh = _CURRENT["cage"]
        else:
            sel = cmds.ls(selection=True) or []
            if sel:
                mesh = _resolve_transform(sel[0])
    if target is None:
        target = _CURRENT.get("target")
    if mesh is None or not cmds.objExists(mesh):
        cmds.warning("QuadWrap: no mesh to color. Select or commit a cage.")
        return None
    if target is None or not cmds.objExists(target):
        try:
            target, _ = get_target_mesh()
        except Exception:  # noqa: BLE001
            target = None
    if target is None or not cmds.objExists(target):
        cmds.warning("QuadWrap: no target mesh loaded.")
        return None
    if om2 is None:
        cmds.warning("QuadWrap: OpenMaya2 unavailable; heat map needs it.")
        return None
    if clamp is None:
        clamp = 0.05
    try:
        clamp = float(clamp)
    except (TypeError, ValueError):
        clamp = 0.05
    clamp = max(1e-6, clamp)
    return _shape_of(mesh), _shape_of(target), clamp


def heat_map(mesh=None, target=None, clamp=None):
    """Color each vertex of `mesh` by its distance to `target` surface.

    Green = on surface, red = >= clamp away. Uses vertex colors (CPV) and
    enables viewport vertex-color display. Returns True on success.
    """
    _require_maya()
    resolved = _resolve_heat_inputs(mesh, target, clamp)
    if resolved is None:
        return False
    mesh_shape, target_shape, clamp = resolved

    cmds.undoInfo(openChunk=True)
    try:
        # read all vertex positions (world space, flattened)
        n = int(cmds.polyEvaluate(mesh_shape, vertex=True))
        flat = cmds.xform("{}.vtx[*]".format(mesh_shape), query=True,
                          worldSpace=True, translation=True) or []
        if len(flat) < 3 * n:
            cmds.warning("QuadWrap: could not read mesh vertices.")
            return False

        # target mesh function
        sel = om2.MSelectionList()
        sel.add(target_shape)
        target_dag = sel.getDagPath(0)
        target_fn = om2.MFnMesh(target_dag)

        dists = [0.0] * n
        for i in range(n):
            p = om2.MPoint(flat[3 * i], flat[3 * i + 1], flat[3 * i + 2])
            try:
                cp, _fid = target_fn.getClosestPoint(p, om2.MSpace.kWorld)
                vec = cp - p
                dists[i] = float(vec.length())
            except Exception:  # noqa: BLE001
                dists[i] = 0.0

        # enable vertex color display on the shape
        try:
            cmds.setAttr(mesh_shape + ".displayColors", 1)
        except Exception:  # noqa: BLE001
            pass

        # colorize. Build flattened component strings for speed? Use per-vertex
        # command (robust; cages are modest vertex counts).
        for i, d in enumerate(dists):
            t = max(0.0, min(1.0, d / clamp))
            if t < 0.5:
                r = 2.0 * t
                g = 1.0
                b = 0.0
            else:
                r = 1.0
                g = 1.0 - 2.0 * (t - 0.5)
                b = 0.0
            try:
                cmds.polyColorPerVertex("{}.vtx[{}]".format(mesh_shape, i),
                                        rgb=(r, g, b), cdo=True)
            except Exception:  # noqa: BLE001
                continue

        # report worst
        if dists:
            worst = max(dists)
            cmds.inViewMessage(statusMessage="QuadWrap heat map: max drift {:.4f}".format(worst),
                              fade=True)
        _set_status("Heat map applied (clamp {:.3f}).".format(clamp))
        return True
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: heat map failed: {}".format(exc))
        traceback.print_exc()
        return False
    finally:
        cmds.undoInfo(closeChunk=True)


def deform_test(mesh=None):
    """Bind `mesh` to two test joints and bend one, so loops can be inspected."""
    _require_maya()
    if mesh is None:
        if COMMITTED:
            mesh = COMMITTED[-1]
        elif _CURRENT.get("cage"):
            mesh = _CURRENT["cage"]
        else:
            sel = cmds.ls(selection=True) or []
            if sel:
                mesh = _resolve_transform(sel[0])
    if not mesh or not cmds.objExists(mesh):
        cmds.warning("QuadWrap: select or commit a cage first.")
        return False
    mesh_shape = _shape_of(mesh)
    if mesh_shape is None:
        cmds.warning("QuadWrap: not a mesh.")
        return False

    # clean any prior test nodes from this run
    for n in ("qw_test_joint_b", "qw_test_joint_a", "qw_test_skin"):
        if cmds.objExists(n):
            try:
                cmds.delete(n)
            except Exception:  # noqa: BLE001
                pass

    cmds.undoInfo(openChunk=True)
    try:
        bb = cmds.exactWorldBoundingBox(mesh)
        cx = (bb[0] + bb[3]) / 2.0
        cy = (bb[1] + bb[4]) / 2.0
        cz = (bb[2] + bb[5]) / 2.0
        # place joints at lower and upper regions
        # Maya parents a new joint under whatever is selected, which would
        # leave the rig behind when "Clear All" deletes the mesh. Unparent
        # them to the world so the rig is ours to clean up.
        j1 = cmds.joint(name="qw_test_joint_a", position=(cx, bb[1], cz))
        if cmds.listRelatives(j1, parent=True):
            try:
                cmds.parent(j1, world=True)
            except Exception:  # noqa: BLE001
                pass
        j2 = cmds.joint(name="qw_test_joint_b", position=(cx, bb[4], cz))
        if cmds.listRelatives(j2, parent=True):
            try:
                cmds.parent(j2, world=True)
            except Exception:  # noqa: BLE001
                pass
        # keep the chain: bending the upper joint bends the mesh
        try:
            cmds.parent(j2, j1)
        except Exception:  # noqa: BLE001
            pass
        # bind
        try:
            skin = cmds.skinCluster(j1, j2, mesh, name="qw_test_skin",
                                    toSelectedBones=True, maximumInfluences=2,
                                    normalizeWeights=True)[0]
        except Exception:  # noqa: BLE001
            cmds.select([j1, j2, mesh], replace=True)
            skin = cmds.skinCluster(name="qw_test_skin",
                                    maximumInfluences=2,
                                    normalizeWeights=True)[0]
        # bend the upper joint
        try:
            cmds.setAttr(j2 + ".rotateZ", 45.0)
        except Exception:  # noqa: BLE001
            try:
                cmds.setAttr(j2 + ".rotateX", 45.0)
            except Exception:  # noqa: BLE001
                pass
        _set_status("Deform test applied (rotate joint to inspect loops).")
        cmds.inViewMessage(statusMessage="QuadWrap deform test: bend qw_test_joint_b",
                          fade=True)
        ACTION_STACK.append("deform:rig")
        return True
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: deform test failed: {}".format(exc))
        traceback.print_exc()
        return False
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
CTRL = {}


def _row(label, ctrl):
    pass


def show_ui():
    _require_maya()
    win = "quadWrapWin"
    if cmds.window(win, exists=True):
        cmds.deleteUI(win)
    cmds.window(win, title="QuadWrap - Semi-Auto Retopology",
                widthHeight=(460, 640), sizeable=True)
    cmds.scrollLayout("qwScroll", childResizable=True)
    cmds.columnLayout("qwCol", adjustableColumn=True, rowSpacing=4)

    cmds.frameLayout(label="Mesh", collapsable=True, marginWidth=8)
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(120, 280))
    cmds.button(label="Load Target Mesh", command=lambda *_: _ui_load_target())
    CTRL["target_lbl"] = cmds.text(label="target: (none)", align="left")
    cmds.setParent("..")
    cmds.text(label="Guide: select vertices -> Draw Strip", font="smallPlainLabelFont")
    cmds.text(label="Fit: load target, then select cage mesh -> Fit to Surface",
              font="smallPlainLabelFont")
    cmds.setParent("..")

    cmds.frameLayout(label="Guide Strips", collapsable=True, marginWidth=8)
    cmds.rowLayout(numberOfColumns=4, columnWidth4=(90, 90, 90, 90))
    cmds.button(label="Draw Strip", command=lambda *_: _ui_draw_strip())
    cmds.button(label="Close Loop", command=lambda *_: _ui_close_loop())
    cmds.button(label="Auto-Ring", command=lambda *_: _ui_auto_ring())
    cmds.button(label="Mirror", command=lambda *_: _ui_mirror())
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(label="Fit to Surface", collapsable=True, marginWidth=8)
    CTRL["proj"] = cmds.floatSliderGrp(
        "qwProj", label="Projection Strength", field=True, minValue=0.0,
        maxValue=1.0, value=_SETTINGS["projection_strength"], precision=2,
        changeCommand=lambda v: _ui_setting_changed("projection_strength", float(v)))
    CTRL["density"] = cmds.floatSliderGrp(
        "qwDensity", label="Density", field=True, minValue=0.1,
        maxValue=5.0, value=_SETTINGS["density"], precision=2,
        changeCommand=lambda v: _ui_setting_changed("density", float(v)))
    CTRL["sym"] = cmds.floatSliderGrp(
        "qwSym", label="Symmetry Blend", field=True, minValue=0.0,
        maxValue=1.0, value=_SETTINGS["symmetry_blend"], precision=2,
        changeCommand=lambda v: _ui_setting_changed("symmetry_blend", float(v)))
    CTRL["targetn"] = cmds.intFieldGrp(
        "qwVertexTarget", label="Vertex Target", numberOfFields=1,
        value1=_SETTINGS["vertex_target"],
        changeCommand=lambda *_: _ui_setting_changed(
            "vertex_target",
            cmds.intFieldGrp("qwVertexTarget", q=True, value1=True)))
    CTRL["snap"] = cmds.floatFieldGrp(
        "qwSnap", label="Snap Offset/Tolerance", numberOfFields=1,
        value1=_SETTINGS["snap_offset"],
        changeCommand=lambda *_: _ui_setting_changed(
            "snap_offset",
            cmds.floatFieldGrp("qwSnap", q=True, value1=True)))
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(130, 260))
    cmds.button(label="Fit to Surface",
                command=lambda *_: _ui_fit())
    CTRL["mode_menu"] = cmds.optionMenuGrp("qwMode", label="Projection Mode",
                                          columnWidth2=(110, 150),
                                          changeCommand=lambda v: _ui_mode_changed(v))
    # Maya's shrinkWrap ".projection" has exactly 5 enum entries.
    for lbl in ("Toward Inner", "Toward Center", "Parallel", "Vertex Normals",
                "Closest"):
        cmds.menuItem(label=lbl)
    try:
        cmds.optionMenuGrp("qwMode", edit=True,
                           select=int(_SETTINGS["projection_mode"]) + 1)
    except Exception:  # noqa: BLE001
        pass
    cmds.setParent("..")
    CTRL["cino"] = cmds.checkBoxGrp(
        "qwCino", label="Closest If No Intersection",
        value1=bool(_SETTINGS["closest_if_no_intersection"]),
        changeCommand=lambda *_: _ui_cino_changed(
            cmds.checkBoxGrp("qwCino", q=True, value1=True)))
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(label="Presets", collapsable=True, marginWidth=8)
    cmds.rowLayout(numberOfColumns=1, adjustableColumn=1)
    CTRL["preset_menu"] = cmds.optionMenuGrp("qwPreset", label="Preset",
        columnWidth2=(60, 250),
        changeCommand=lambda v: _ui_preset_selected(v))
    for name in list(PRESETS.keys()) + list(_CUSTOM_PRESETS.keys()):
        cmds.menuItem(label=name)
    if _SETTINGS["preset"] in list(PRESETS.keys()) + list(_CUSTOM_PRESETS.keys()):
        try:
            idx = (list(PRESETS.keys()) + list(_CUSTOM_PRESETS.keys())).index(
                _SETTINGS["preset"]) + 1
            cmds.optionMenuGrp("qwPreset", edit=True, select=idx)
        except Exception:  # noqa: BLE001
            pass
    cmds.setParent("..")
    cmds.button(label="Save Current Settings",
                command=lambda *_: _ui_save_preset())
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(label="Actions", collapsable=True, marginWidth=8)
    cmds.rowLayout(numberOfColumns=4, columnWidth4=(90, 90, 90, 90))
    cmds.button(label="Preview", command=lambda *_: _ui_preview())
    cmds.button(label="Commit", command=lambda *_: _ui_commit())
    cmds.button(label="Undo Last", command=lambda *_: _ui_undo_last())
    cmds.button(label="Clear All", command=lambda *_: _ui_clear_all())
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(label="Week-3 extras", collapsable=True, marginWidth=8)
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(190, 190))
    cmds.button(label="Heat Map (deviation)",
                command=lambda *_: _ui_heat_map())
    cmds.button(label="Deform Test (skin)",
                command=lambda *_: _ui_deform_test())
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(label="Stats", collapsable=True, marginWidth=8)
    CTRL["stats_txt"] = cmds.text(label="poly: -   verts: -   guides: 0   non-manifold: 0",
                                 align="left")
    cmds.button(label="Refresh Stats", command=lambda *_: _ui_refresh_stats())
    cmds.setParent("..")

    CTRL["status_lbl"] = cmds.text(label="Ready.", align="left",
                                  font="smallPlainLabelFont")

    cmds.showWindow(win)
    _ui_refresh_stats()
    _set_status("Ready. Load a target mesh to begin.")


def _set_status(msg):
    if "status_lbl" in CTRL and CTRL["status_lbl"]:
        try:
            cmds.text(CTRL["status_lbl"], edit=True, label=str(msg))
        except Exception:  # noqa: BLE001
            pass


def _ui_load_target():
    try:
        tr, shape = get_target_mesh()
        if tr is None:
            _set_status("Load failed: select one mesh.")
            return
        if "target_lbl" in CTRL:
            cmds.text(CTRL["target_lbl"], edit=True, label="target: " + str(tr))
        _set_status("Target loaded: " + str(tr))
        _ui_refresh_stats()
        save_settings()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_draw_strip():
    try:
        name = create_strip_from_selection()
        if name:
            _set_status("Strip created: " + name)
        else:
            _set_status("Strip failed (select 2+ vertices).")
        _ui_refresh_stats()
        save_settings()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_close_loop():
    try:
        if close_loop():
            _set_status("Loop closed.")
        else:
            _set_status("Close loop failed.")
        _ui_refresh_stats()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_auto_ring():
    try:
        sel = cmds.ls(selection=True) or []
        guide = sel[0] if sel else None
        made = auto_ring(guide=guide)
        _set_status("Auto-Ring created {} strips.".format(made))
        _ui_refresh_stats()
        save_settings()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_mirror():
    try:
        sel = cmds.ls(selection=True) or []
        guide = sel[0] if sel else None
        dup = mirror_guide(guide=guide)
        if dup:
            _set_status("Mirrored: " + dup)
        else:
            _set_status("Mirror failed.")
        _ui_refresh_stats()
        save_settings()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_setting_changed(key, value):
    try:
        _SETTINGS[key] = value
        _SETTINGS["preset"] = "Custom"
        # clamp to legal ranges so the UI can never write an invalid value
        _SETTINGS.update(_sanitize_settings(_SETTINGS))
        # live-update fit node if present
        node = _CURRENT.get("sw_node")
        if node and cmds.objExists(node):
            _apply_settings_to_node(node)
        # refresh preset menu label best-effort
        save_settings()
    except Exception:  # noqa: BLE001
        pass


def _ui_mode_changed(value):
    # map menu label -> index (must match Maya's 5-entry shrinkWrap enum)
    mapping = {
        "Toward Inner": 0, "Toward Center": 1, "Parallel": 2,
        "Vertex Normals": 3, "Closest": 4,
    }
    idx = mapping.get(value, _SETTINGS["projection_mode"])
    _ui_setting_changed("projection_mode", idx)


def _ui_cino_changed(value):
    """'Closest If No Intersection' is a separate boolean on shrinkWrap."""
    _ui_setting_changed("closest_if_no_intersection", 1 if value else 0)


def _ui_preset_selected(value):
    if apply_preset(value):
        # push settings back into UI controls
        try:
            cmds.floatSliderGrp("qwProj", edit=True, value=_SETTINGS["projection_strength"])
            cmds.floatSliderGrp("qwDensity", edit=True, value=_SETTINGS["density"])
            cmds.floatSliderGrp("qwSym", edit=True, value=_SETTINGS["symmetry_blend"])
            cmds.intFieldGrp("qwVertexTarget", edit=True, value1=_SETTINGS["vertex_target"])
            cmds.floatFieldGrp("qwSnap", edit=True, value1=_SETTINGS["snap_offset"])
        except Exception:  # noqa: BLE001
            pass
        _set_status("Preset applied: " + value)
        save_settings()
    else:
        _set_status("Unknown preset: " + str(value))


def _ui_save_preset():
    try:
        # ask via small prompt? Use a default name + index
        name = "CustomPreset{}".format(len(_CUSTOM_PRESETS) + 1)
        if save_current_as_preset(name):
            # add menu item
            try:
                cmds.menuItem(label=name, parent="qwPreset|OptionMenu")
            except Exception:  # noqa: BLE001
                pass
            _set_status("Saved preset: " + name)
        else:
            _set_status("Could not save preset.")
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_fit():
    try:
        node = fit_to_surface()
        if node:
            _set_status("Fitted: " + str(node))
        else:
            _set_status("Fit failed (select cage; target loaded?).")
        _ui_refresh_stats()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_preview():
    try:
        g = preview()
        if g:
            _set_status("Preview: " + g)
        else:
            _set_status("Preview failed.")
        _ui_refresh_stats()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_commit():
    try:
        name = commit()
        if name:
            _set_status("Committed: " + name)
        else:
            _set_status("Commit failed.")
        _ui_refresh_stats()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_undo_last():
    try:
        if undo_last():
            _set_status("Undid last action.")
        else:
            _set_status("Nothing to undo.")
        _ui_refresh_stats()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_clear_all():
    try:
        clear_all()
        _set_status("Cleared all created nodes.")
        if "target_lbl" in CTRL:
            cmds.text(CTRL["target_lbl"], edit=True, label="target: (none)")
        _ui_refresh_stats()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_heat_map():
    try:
        heat_map()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_deform_test():
    try:
        deform_test()
    except Exception as exc:  # noqa: BLE001
        cmds.warning("QuadWrap: {}".format(exc))


def _ui_refresh_stats():
    try:
        mesh = _CURRENT.get("cage") or _CURRENT.get("target")
        st = get_stats(mesh)
        if "stats_txt" in CTRL and CTRL["stats_txt"]:
            cmds.text(CTRL["stats_txt"], edit=True,
                      label="poly: {}  verts: {}  guides: {}  non-manifold: {}".format(
                          st["faces"], st["verts"], st["guides"], st["nonmanifold"]))
    except Exception:  # noqa: BLE001
        pass


# run UI when executed in Maya
if __name__ == "__main__" and cmds is not None:
    show_ui()
