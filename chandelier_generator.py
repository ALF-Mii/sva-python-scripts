"""
Chandelier Generator for Maya (Route B - new tool)

Design problem: Environment/lighting artists need a fast placeholder
chandelier that still reads as a chandelier from a distance - central stem,
radiating arms, candles/bulbs - without modeling it by hand. It has to get
right: (1) proportions stay sane as arm count changes, (2) clean hierarchy
for art direction, (3) safe to undo/delete in someone else's scene.

New capabilities vs a single static prop:
- Two kinds: round (radial) and square (cube-frame) styles
- Many where it did one: N arms x M tiers, parametric
- Handled input: clamps/corrects bad UI or script input on purpose

Habits:
- Undo in one step via openChunk/closeChunk
- Delete only what it made via group + registry (no wildcard deletes)
"""
import math

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

# Registry of groups THIS tool created (for safe delete)
CREATED_GROUPS = []
# group name -> [supportMat, supportSG, lightMat, lightSG] (also only ours)
_GROUP_SHADERS = {}
_CHANDELIER_PREFIX = "chandelier_"
_counter = 0


def _require_maya():
    if cmds is None:
        raise RuntimeError("This script must run inside Maya (maya.cmds not found).")


def _sanitize_inputs(num_arms, radius, height, tiers, style, extra_lights=0,
                     center_lights=6, center_height=0.35, center_spread=0.45,
                     support_color=(0.25, 0.25, 0.28),
                     light_color=(1.0, 0.9, 0.7), brightness=1.0,
                     trim_color=(0.88, 0.87, 0.82), metal_color=None):
    """Handle inputs you didn't plan for - on purpose."""
    # metal_color is the new name for support_color (old scripts keep working)
    if metal_color is not None:
        support_color = metal_color
    # num_arms: int 3..12
    try:
        num_arms = int(num_arms)
    except (TypeError, ValueError):
        num_arms = 6
    num_arms = max(3, min(12, num_arms))

    # radius / height: positive floats with sane caps
    try:
        radius = float(radius)
    except (TypeError, ValueError):
        radius = 5.0
    try:
        height = float(height)
    except (TypeError, ValueError):
        height = 8.0
    if not (0.5 <= radius <= 20.0):
        radius = 5.0
    if not (1.0 <= height <= 30.0):
        height = 8.0

    # tiers: 1..5 (was 1-2, now N layers)
    try:
        tiers = int(tiers)
    except (TypeError, ValueError):
        tiers = 1
    tiers = max(1, min(5, tiers))

    # style: 'round' or 'square', default round
    if style not in ("round", "square"):
        style = "round"

    # extra_lights: 0..3 filler lights between each pair of main arms
    try:
        extra_lights = int(extra_lights)
    except (TypeError, ValueError):
        extra_lights = 0
    extra_lights = max(0, min(3, extra_lights))

    # center_lights: 0..12 inner-ring lights hugging the stem per tier
    try:
        center_lights = int(center_lights)
    except (TypeError, ValueError):
        center_lights = 0
    center_lights = max(0, min(12, center_lights))

    # center_height: lift of inner ring above its tier (-2..2, negative = lower)
    try:
        center_height = float(center_height)
    except (TypeError, ValueError):
        center_height = 0.35
    center_height = max(-2.0, min(2.0, center_height))

    # center_spread: inner ring radius as fraction of outer (0.2..0.8).
    # Larger = wider inner ring = more space between center lights
    # and more gap from the stem / closer to the outer ring.
    try:
        center_spread = float(center_spread)
    except (TypeError, ValueError):
        center_spread = 0.45
    center_spread = max(0.2, min(0.8, center_spread))

    support_color = _sanitize_color(support_color, (0.25, 0.25, 0.28))
    light_color = _sanitize_color(light_color, (1.0, 0.9, 0.7))
    trim_color = _sanitize_color(trim_color, (0.88, 0.87, 0.82))

    # brightness: 0..3 incandescence multiplier for the light parts
    try:
        brightness = float(brightness)
    except (TypeError, ValueError):
        brightness = 1.0
    brightness = max(0.0, min(3.0, brightness))

    return (num_arms, radius, height, tiers, style, extra_lights, center_lights,
            center_height, center_spread, support_color, light_color,
            brightness, trim_color)


def _unique_name(style):
    global _counter
    _counter += 1
    return "{}{}_{}".format(_CHANDELIER_PREFIX, style, _counter)


def _make_part(make_fn, *args, **kwargs):
    """Create one primitive, return its transform node."""
    nodes = make_fn(*args, **kwargs)
    # poly* commands return [transform, history]; we want transform
    if isinstance(nodes, (list, tuple)):
        return nodes[0]
    return nodes


def _sanitize_color(value, default):
    """Clamp an RGB input to 3 floats in 0..1. Bad input -> default."""
    try:
        r, g, b = float(value[0]), float(value[1]), float(value[2])
        return (max(0.0, min(1.0, r)),
                max(0.0, min(1.0, g)),
                max(0.0, min(1.0, b)))
    except (TypeError, ValueError, IndexError):
        return default


def _make_lambert(base_name, color, incandescence=(0.0, 0.0, 0.0)):
    """Create a lambert + shading group. Returns (mat, shadingGroup)."""
    mat = cmds.shadingNode("lambert", asShader=True, name=base_name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                   name=base_name + "SG")
    cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
    cmds.setAttr(mat + ".color", color[0], color[1], color[2], type="double3")
    cmds.setAttr(mat + ".incandescence",
                 incandescence[0], incandescence[1], incandescence[2],
                 type="double3")
    return mat, sg


def _assign_material(node, shading_group):
    """Assign a mesh to a shading group via its shapes (robust)."""
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        targets = shapes if shapes else [node]
        for t in targets:
            cmds.sets(t, e=True, forceElement=shading_group)
    except Exception as exc:  # noqa: BLE001 - never fail a build on shading
        cmds.warning("Could not assign {} to {}: {}".format(node,
                                                             shading_group,
                                                             exc))


def _assign_batch(nodes, material):
    """Assign many transforms to a material at once via hyperShade.

    Per-node cmds.sets in a loop proved flaky (only one light kept the
    material), so shading now goes through one hyperShade call per bucket.
    Restores the user's selection afterwards. Returns assigned count.
    """
    live = [n for n in nodes if cmds.objExists(n)]
    if not live:
        return 0
    prev = cmds.ls(selection=True) or []
    try:
        cmds.select(live, replace=True)
        cmds.hyperShade(assign=material)
    finally:
        if prev:
            try:
                cmds.select(prev, replace=True)
            except (RuntimeError, ValueError):
                cmds.select(clear=True)
        else:
            cmds.select(clear=True)
    return len(live)


def _fixture_position(style, k, total, r):
    """Position k-th fixture of total around the support. Returns (x, z, rot_y)."""
    if style == "round":
        angle = (2.0 * math.pi * k) / total
        return math.cos(angle) * r, math.sin(angle) * r, -math.degrees(angle)
    # Square: distribute evenly around the square perimeter
    t = float(k) / total * 4.0  # 0..4
    side_idx = int(t) % 4
    frac = (t % 1.0) - 0.5  # -0.5..0.5 along side
    side = r * 1.4
    if side_idx == 0:
        return frac * side, side / 2, 0
    elif side_idx == 1:
        return frac * side, -side / 2, 0
    elif side_idx == 2:
        return side / 2, frac * side, 90
    else:
        return -side / 2, frac * side, 90


def _build_center_fill(parent_group, outer_r, y, style, tier_scale, count,
                       height_off=0.35, spread=0.45,
                       metal_nodes=None, trim_nodes=None, light_nodes=None,
                       support_nodes=None):
    """Inner ring of lights around the stem - the 'full middle'.

    Each tier gets a small concentric support ring (torus for round,
    cube frame for square) with its own dishes/candles/bulbs standing
    on it, raised slightly so the middle reads as a second layer.
    height_off: vertical lift above the tier. spread: inner radius
    as a fraction of outer radius (controls spacing between lights).
    Metal: ring/arms/dishes. Light: candles + bulbs. Trim unused here.
    Returns list of all nodes.
    """
    if metal_nodes is None:
        metal_nodes = []
    if trim_nodes is None:
        # old callers passed support_nodes=... -> treat as trim bucket
        trim_nodes = support_nodes if support_nodes is not None else []
    if light_nodes is None:
        light_nodes = []
    if count <= 0 or outer_r < 0.6:
        return []
    inner_r = outer_r * spread
    inner_y = y + height_off * tier_scale
    s = tier_scale * 0.7
    parts = []

    def _keep(node, bucket):
        parts.append(node)
        if bucket == "light":
            light_nodes.append(node)
        elif bucket == "trim":
            trim_nodes.append(node)
        else:
            metal_nodes.append(node)
        return node

    if style == "round":
        ring = _make_part(
            cmds.polyTorus, r=inner_r, sr=0.08 * tier_scale, name="centerRing_tmp"
        )
        cmds.move(0, inner_y, 0, ring)
        cmds.parent(ring, parent_group)
        _keep(ring, "metal")
    else:
        side = inner_r * 1.4
        beam_h = 0.12
        for rot_y, pos in [
            (0, (0, inner_y, side / 2)),
            (0, (0, inner_y, -side / 2)),
            (90, (side / 2, inner_y, 0)),
            (90, (-side / 2, inner_y, 0)),
        ]:
            beam = _make_part(
                cmds.polyCube, w=side, h=beam_h, d=beam_h, name="centerBeam_tmp"
            )
            cmds.move(pos[0], pos[1], pos[2], beam)
            if rot_y:
                cmds.rotate(0, rot_y, 0, beam)
            cmds.parent(beam, parent_group)
            _keep(beam, "metal")

    for k in range(count):
        x, z, rot_y = _fixture_position(style, k, count, inner_r)

        # Short arm from stem out to the inner ring (so nothing floats)
        arm_len = math.sqrt(x * x + z * z)
        if arm_len > 0.01:
            arm = _make_part(
                cmds.polyCylinder,
                r=0.06 * tier_scale,
                h=arm_len,
                sz=8,
                name="centerArm_tmp",
            )
            cmds.rotate(0, 0, 90, arm)
            cmds.rotate(0, rot_y, 0, arm)
            cmds.move(x / 2.0, inner_y, z / 2.0, arm)
            cmds.parent(arm, parent_group)
            _keep(arm, "metal")

        dish = _make_part(
            cmds.polyCylinder, r=0.22 * s + 0.05, h=0.08, sz=10, name="centerDish_tmp"
        )
        cmds.move(x, inner_y + 0.1, z, dish)
        cmds.parent(dish, parent_group)
        _keep(dish, "metal")

        candle = _make_part(
            cmds.polyCylinder, r=0.10 * s + 0.02, h=0.6, sz=8, name="centerCandle_tmp"
        )
        cmds.move(x, inner_y + 0.4, z, candle)
        cmds.parent(candle, parent_group)
        _keep(candle, "light")

        bulb = _make_part(
            cmds.polySphere, r=0.15 * s + 0.03, sx=10, sy=8, name="centerBulb_tmp"
        )
        cmds.move(x, inner_y + 0.85, z, bulb)
        cmds.parent(bulb, parent_group)
        _keep(bulb, "light")

    return parts


def _build_tier(
    parent_group, num_arms, radius, y, style, tier_scale=1.0, extra_lights=0,
    center_lights=0, center_height=0.35, center_spread=0.45,
    metal_nodes=None, trim_nodes=None, light_nodes=None,
    support_nodes=None
):
    """Build one tier: outer arms + inner 'full middle' ring."""
    if metal_nodes is None:
        metal_nodes = []
    if trim_nodes is None:
        # old callers passed support_nodes=... -> metal bucket used to live there;
        # trim hardware now has its own bucket, so start fresh unless given.
        trim_nodes = []
    if light_nodes is None:
        light_nodes = []
    # backward compat: support_nodes=... meant the old combined bucket
    if support_nodes is not None and not metal_nodes and not trim_nodes:
        metal_nodes = support_nodes
    r = radius * tier_scale
    parts = []

    def _keep(node, bucket):
        parts.append(node)
        if bucket == "light":
            light_nodes.append(node)
        elif bucket == "trim":
            trim_nodes.append(node)
        else:
            metal_nodes.append(node)
        return node

    if style == "round":
        # Round support ring: torus so dishes/candles stand on something,
        # matching the square style's cube-beam frame.
        ring = _make_part(
            cmds.polyTorus, r=r, sr=0.12 * tier_scale, name="ring_tmp"
        )
        cmds.move(0, y, 0, ring)
        cmds.parent(ring, parent_group)
        _keep(ring, "metal")

    if style == "square":
        # Square frame: 4 thin cubes forming a square of side r
        side = r * 1.4
        beam_h = 0.15
        for rot_y, pos in [
            (0, (0, y, side / 2)),
            (0, (0, y, -side / 2)),
            (90, (side / 2, y, 0)),
            (90, (-side / 2, y, 0)),
        ]:
            beam = _make_part(
                cmds.polyCube, w=side, h=beam_h, d=beam_h, name="beam_tmp"
            )
            cmds.move(pos[0], pos[1], pos[2], beam)
            if rot_y:
                cmds.rotate(0, rot_y, 0, beam)
            cmds.parent(beam, parent_group)
            _keep(beam, "metal")

    # Middle fullness: inner concentric ring around the stem.
    parts.extend(
        _build_center_fill(parent_group, r, y, style, tier_scale, center_lights,
                           height_off=center_height, spread=center_spread,
                           metal_nodes=metal_nodes,
                           trim_nodes=trim_nodes,
                           light_nodes=light_nodes)
    )

    # Main arms + filler lights share the same support positions.
    # e.g. num_arms=6, extra_lights=1 -> 12 positions, every 2nd is a main arm.
    step = extra_lights + 1
    total = num_arms * step
    for k in range(total):
        is_main = (k % step == 0)
        x, z, rot_y = _fixture_position(style, k, total, r)

        if is_main:
            # Horizontal arm: thin cylinder from center to (x, z)
            arm_len = math.sqrt(x * x + z * z)
            arm = _make_part(
                cmds.polyCylinder,
                r=0.08 * tier_scale,
                h=arm_len,
                sz=8,
                name="arm_tmp",
            )
            # Cylinder axis is Y; rotate to lie flat pointing outward
            cmds.rotate(0, 0, 90, arm)
            cmds.rotate(0, rot_y, 0, arm)
            cmds.move(x / 2.0, y, z / 2.0, arm)
            cmds.parent(arm, parent_group)
            _keep(arm, "metal")
            s = tier_scale
        else:
            # Filler light: no arm, stands directly on the ring/frame.
            s = tier_scale * 0.8

        # Candle holder dish: short wide cylinder (metal)
        dish = _make_part(
            cmds.polyCylinder, r=0.28 * s, h=0.08, sz=12, name="dish_tmp"
        )
        cmds.move(x, y + 0.1, z, dish)
        cmds.parent(dish, parent_group)
        _keep(dish, "metal")

        # Candle stick: thin tall cylinder (shorter for fillers) (light)
        candle_h = 0.9 if is_main else 0.6
        candle = _make_part(
            cmds.polyCylinder, r=0.12 * s, h=candle_h, sz=8, name="candle_tmp"
        )
        cmds.move(x, y + 0.1 + candle_h / 2.0, z, candle)
        cmds.parent(candle, parent_group)
        _keep(candle, "light")

        # Bulb / flame: sphere (light - the top)
        bulb = _make_part(
            cmds.polySphere, r=0.18 * s, sx=10, sy=8, name="bulb_tmp"
        )
        cmds.move(x, y + 0.1 + candle_h + 0.18 * s, z, bulb)
        cmds.parent(bulb, parent_group)
        _keep(bulb, "light")

    return parts


def build_chandelier(
    num_arms=6, radius=5.0, height=8.0, tiers=1, style="round", extra_lights=0,
    center_lights=6, center_height=0.35, center_spread=0.45,
    support_color=(0.25, 0.25, 0.28), light_color=(1.0, 0.9, 0.7),
    brightness=1.0, trim_color=(0.88, 0.87, 0.82), metal_color=None
):
    """
    Build a chandelier from primitives. All geometry is parented under
    one group so delete is safe. Undo is one chunk.

    tiers: 1..5 layers, stacked bottom (large) to top (small).
    extra_lights: 0..3 filler candles between each pair of main arms,
        standing directly on the torus ring / square frame.
    center_lights: 0..12 inner-ring lights per tier hugging the stem,
        each with its own support ring underneath. This is the 'full middle'.
    center_height: -2..2 lift of the inner ring above its tier.
        Negative drops it below for a lower center layer.
    center_spread: 0.2..0.8 inner radius as fraction of outer radius.
        Larger spreads lights further apart.
    support_color / metal_color: RGB 0..1 for arms/rings/dishes (metal).
    trim_color: RGB 0..1 for non-metal hardware: ceiling mount, stem,
        collars, finial.
    light_color: RGB 0..1 for all lights: candles + bulbs.
    brightness: 0..3 incandescence multiplier for the light parts.

    Returns the group name.
    """
    _require_maya()
    if metal_color is not None:
        support_color = metal_color
    (num_arms, radius, height, tiers, style, extra_lights, center_lights,
     center_height, center_spread, metal_color, light_color,
     brightness, trim_color) = _sanitize_inputs(
        num_arms, radius, height, tiers, style, extra_lights, center_lights,
        center_height, center_spread, support_color, light_color, brightness,
        trim_color
    )

    group_name = _unique_name(style)

    cmds.undoInfo(openChunk=True)
    try:
        grp = cmds.group(empty=True, name=group_name)
        metal_nodes = []
        trim_nodes = []
        light_nodes = []

        # Ceiling mount: flat cylinder at top (trim, not metal)
        mount = _make_part(cmds.polyCylinder, r=0.6, h=0.3, sz=16, name="mount_tmp")
        cmds.move(0, height, 0, mount)
        cmds.parent(mount, grp)
        trim_nodes.append(mount)

        # Stem: long thin cylinder (trim hardware)
        stem = _make_part(cmds.polyCylinder, r=0.12, h=height, sz=10, name="stem_tmp")
        cmds.move(0, height / 2.0, 0, stem)
        cmds.parent(stem, grp)
        trim_nodes.append(stem)

        # Middle stays open for layers: slim stem only, plus a small
        # collar cylinder where each tier meets the stem. No large
        # central spheres blocking the stack.
        # Tier(s): bottom = large, top = small. Legacy 1-2 tier
        # heights preserved; N>2 spreads evenly 0.35 -> 0.70.
        if tiers == 1:
            tier_specs = [(height * 0.45, 1.0)]
        elif tiers == 2:
            tier_specs = [
                (height * 0.45, 1.0),
                (height * 0.65, 0.65),
            ]
        else:
            tier_specs = []
            for j in range(tiers):
                t = j / float(tiers - 1)  # 0 bottom -> 1 top
                tier_specs.append(
                    (height * (0.35 + 0.35 * t), 1.0 - 0.35 * t)
                )
        for ty, ts in tier_specs:
            collar = _make_part(
                cmds.polyCylinder,
                r=0.25 * ts + 0.05,
                h=0.2,
                sz=12,
                name="collar_tmp",
            )
            cmds.move(0, ty, 0, collar)
            cmds.parent(collar, grp)
            trim_nodes.append(collar)

        for ty, ts in tier_specs:
            _build_tier(grp, num_arms, radius, ty, style, tier_scale=ts,
                        extra_lights=extra_lights, center_lights=center_lights,
                        center_height=center_height,
                        center_spread=center_spread,
                        metal_nodes=metal_nodes,
                        trim_nodes=trim_nodes,
                        light_nodes=light_nodes)

        # Bottom finial: sphere under lowest tier (trim)
        finial_y = tier_specs[0][0] - 0.8
        finial = _make_part(cmds.polySphere, r=0.4, sx=12, sy=8, name="finial_tmp")
        cmds.move(0, finial_y, 0, finial)
        cmds.parent(finial, grp)
        trim_nodes.append(finial)

        # Three materials: metal + trim + light-with-brightness.
        # Batch hyperShade assign per bucket (looped sets only kept one).
        glow = (light_color[0] * brightness,
                light_color[1] * brightness,
                light_color[2] * brightness)
        metal_mat, metal_sg = _make_lambert(
            grp + "_metalMat", metal_color, (0.0, 0.0, 0.0))
        trim_mat, trim_sg = _make_lambert(
            grp + "_trimMat", trim_color, (0.0, 0.0, 0.0))
        light_mat, light_sg = _make_lambert(
            grp + "_lightMat", light_color, glow)
        _GROUP_SHADERS[grp] = [metal_mat, metal_sg, trim_mat, trim_sg,
                               light_mat, light_sg]
        n_metal = _assign_batch(metal_nodes, metal_mat)
        n_trim = _assign_batch(trim_nodes, trim_mat)
        n_light = _assign_batch(light_nodes, light_mat)
        if n_light != len(light_nodes):
            cmds.warning(
                "Light assign: {} of {} got {}".format(
                    n_light, len(light_nodes), light_mat))

        CREATED_GROUPS.append(grp)
        return grp
    finally:
        cmds.undoInfo(closeChunk=True)


def delete_chandelier(group_name):
    """Delete ONLY the group this tool made + its three materials."""
    _require_maya()
    if group_name not in CREATED_GROUPS:
        # Also allow prefix match only if we created it this session;
        # otherwise refuse - protects tree_oak_final-type accidents.
        raise ValueError(
            "Refusing to delete '{}': not created by this tool.".format(group_name)
        )
    if cmds.objExists(group_name):
        cmds.delete(group_name)
    # Clean up our own shaders (tracked per group, never wildcard).
    for node in _GROUP_SHADERS.pop(group_name, []):
        try:
            if cmds.objExists(node):
                cmds.delete(node)
        except (RuntimeError, ValueError):
            pass
    CREATED_GROUPS.remove(group_name)


def delete_last_chandelier():
    """Delete the most recently created chandelier."""
    _require_maya()
    if not CREATED_GROUPS:
        cmds.warning("No chandeliers created by this tool to delete.")
        return
    delete_chandelier(CREATED_GROUPS[-1])


def show_ui():
    """Simple control window."""
    _require_maya()
    win = "chandelierWin"
    if cmds.window(win, exists=True):
        cmds.deleteUI(win)
    cmds.window(win, title="Chandelier Generator")
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
    cmds.intSliderGrp("armsSlider", label="Arms", min=3, max=12, value=6, field=True)
    cmds.floatSliderGrp(
        "radiusSlider", label="Radius", min=1.0, max=15.0, value=5.0, field=True
    )
    cmds.floatSliderGrp(
        "heightSlider", label="Height", min=2.0, max=20.0, value=8.0, field=True
    )
    cmds.intSliderGrp("tiersSlider", label="Tiers", min=1, max=5, value=1, field=True)
    cmds.intSliderGrp(
        "extraSlider", label="Extra lights", min=0, max=3, value=0, field=True
    )
    cmds.intSliderGrp(
        "centerSlider", label="Center lights", min=0, max=12, value=6, field=True
    )
    cmds.floatSliderGrp(
        "centerHeightSlider", label="Center height", min=-2.0, max=2.0,
        value=0.35, field=True, precision=2
    )
    cmds.floatSliderGrp(
        "centerSpreadSlider", label="Center spread", min=0.2, max=0.8,
        value=0.45, field=True, precision=2
    )
    cmds.colorSliderGrp("metalColor", label="Metal color",
                        rgb=(0.25, 0.25, 0.28))
    cmds.colorSliderGrp("trimColor", label="Trim color",
                        rgb=(0.88, 0.87, 0.82))
    cmds.colorSliderGrp("lightColor", label="Light color",
                        rgb=(1.0, 0.9, 0.7))
    cmds.floatSliderGrp(
        "brightSlider", label="Brightness", min=0.0, max=3.0,
        value=1.0, field=True, precision=2
    )
    cmds.optionMenuGrp("styleMenu", label="Style")
    cmds.menuItem(label="round")
    cmds.menuItem(label="square")
    cmds.button(
        label="Build Chandelier",
        command=lambda *_: build_chandelier(
            cmds.intSliderGrp("armsSlider", q=True, value=True),
            cmds.floatSliderGrp("radiusSlider", q=True, value=True),
            cmds.floatSliderGrp("heightSlider", q=True, value=True),
            cmds.intSliderGrp("tiersSlider", q=True, value=True),
            cmds.optionMenuGrp("styleMenu", q=True, value=True),
            cmds.intSliderGrp("extraSlider", q=True, value=True),
            cmds.intSliderGrp("centerSlider", q=True, value=True),
            cmds.floatSliderGrp("centerHeightSlider", q=True, value=True),
            cmds.floatSliderGrp("centerSpreadSlider", q=True, value=True),
            cmds.colorSliderGrp("metalColor", q=True, rgb=True),
            cmds.colorSliderGrp("lightColor", q=True, rgb=True),
            cmds.floatSliderGrp("brightSlider", q=True, value=True),
            cmds.colorSliderGrp("trimColor", q=True, rgb=True),
        ),
    )
    cmds.button(label="Undo Last (Delete)", command=lambda *_: delete_last_chandelier())
    cmds.showWindow(win)


# Run UI when executed inside Maya; otherwise no-op with a clear message.
if __name__ == "__main__" and cmds is not None:
    show_ui()
