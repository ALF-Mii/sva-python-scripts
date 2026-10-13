# QuadWrap — Deep Design (pre-implementation)

Status: design only, no code yet. Builds on:
- Pitch `QuadWrap — Semi-Automatic Retopology for Maya`
- Instructor feedback (Isaac): leverage Maya's **Shrink Wrap deformer** for projection; benchmark against `polyRetopo`; add **heat-map deviation**, **curvature-adaptive density**, and a **deformation test**.

---

## 1. One-paragraph goal

QuadWrap is a Maya 2027 Python tool that takes a high-poly target mesh and a coarse hand-guided cage (strips/loops), then **fits the cage onto the target live and non-destructively using a Shrink Wrap deformer** the tool owns and drives from its panel. It previews as a ghost mesh, commits only on demand (baking to clean geometry), and ships with presets, persisted settings, single-undo actions, a deviation heat map, optional curvature-aware subdivision, and a skinning deformation check. It deliberately turns Maya's existing deformer into the "risky projection core" so the code we write is control/plumbing UX, which is what this class grades.

## 2. Key pivot from the pitch

| Pitch idea | Realistic implementation based on instructor feedback |
|---|---|
| Write projection code from scratch | **Don't.** Create `cmds.deformer(type="shrinkWrap")`, connect target `.worldMesh[0] -> .targetGeom`, drive `.envelope` from "Projection Strength". Deformer is live, undoable, non-destructive by nature. |
| Riskiest part = snapping | Retained as risk #2 (cage quality), but shrink wrap removes the math risk. |
| "Draw Strip" interactive drawing | Implement via Maya-native input (strips from selected vertex rows) + transforms; optionally invoke Maya's curve tool. Full freehand drawing in pure Python is out of scope. |
| Auto-Ring | Duplicate a strip N times around a chosen axis using `cmds.duplicate` + rotate. |
| Undo "single action" | Wrap generation/commit in `openChunk`/`closeChunk`; guide edits are tracked for "Undo Last". |
| Heat map | Compute per-vertex distance from committed cage to target; write CPV; display with a ramp material or viewport CPV. |
| Curvature density | Sample curvature along guide curves; subdivide more where turn rate is high. Phase-3 stretch. |
| Deformation check | Bind committed result to 2 test joints via `skinCluster`, rotate a joint, leave mesh deformed so loops can be inspected. |

## 3. Architecture

Single module `quadwrap.py`, matching repo style (`chandelier_generator.py`): one file, importable, `show_ui()`. Sections:

```
quadwrap.py
├─ constants / registry (CREATED_GUIDES, COMMITTED, _GROUPS)
├─ _require_maya
├─ sanitize_*  (input clamping, selection validation)
├─ settings     (load/save JSON, presets)
├─ selection    (get target mesh; reject bad)
├─ guides       (create strip from vertex selection; mirror; ring; close loop)
├─ shrinkwrap   (create node; attach target; set params; envelope)
├─ preview      (ghost mesh update)
├─ commit       (bake; register; cleanup)
├─ heat map
├─ curvature (stretch)
├─ deform test
├─ stats / non-manifold
└─ show_ui
```

No external dependencies beyond `maya.cmds`, `maya.OpenMaya` (for closest-point/CPV), `json`, `os`.

## 4. Shrink-wrap driver (the projection core)

```
node = cmds.deformer(cage_shape, type="shrinkWrap")[0]
cmds.connectAttr(target_shape+".worldMesh[0]", node+".targetGeom", f=True)
# attributes we expose:
#   .envelope              <- Projection Strength (0..1)
#   .projection            <- mode selector (default "closest" or "vertex normals")
#   .offset                <- Snap Offset/Tolerance
#   .targetSmoothLevel     <- Density influence (optional)
#   .closestIfNoIntersection <- True (robustness on messy sculpts)
#   .bidirectional         <- toggle
```

Lifecycle: one cage mesh = one shrinkWrap node tracked in module state. Re-applying recreates/cleans. Detach = delete node (cage returns to original). Preview ghost = duplicate + apply same node settings (or share node via deformer add).

Notes:
- Connection pattern (from Maya docs): target_shape + ".worldMesh[0]" -> node + ".targetGeom"; plus `.co/.suv/.kb/...` if we want smooth-target behaviors. Minimal: worldMesh only.
- To make it one undo step on detach/commit: wrap in chunk.

## 5. Guide strip model

Represent each guide strip as a named transform (a thin ribbon mesh generated from a vertex row) registered in `CREATED_GUIDES`. Metadata kept in a dict keyed by name:
```
GUIDE_META[name] = {
  "source_verts": [...], "axis": "x", "mirrored": bool, "loop": bool
}
```

Creation flow:
- User selects a row of vertices on the *target* mesh in order.
- "Draw Strip" -> build a ribbon mesh along those verts (extrude normal), name `qw_strip_N`.
- "Close Loop" connects endpoint verts into a cyclic strip.
- "Auto-Ring" duplicates the strip around chosen axis.
- "Mirror" (via Symmetry Blend) duplicates + scales -1, and blends vertex positions toward mirror when > 0.

Fallback: if row count < 2, warn and abort. If strip self-intersects, flag in stats but don't crash.

Curve-to-cage: provide "Build Cage from Strips" which lofts selected strips pairwise into a quad-ish mesh (best-effort; user can also supply their own cage). Keep optional.

## 6. Settings & presets

JSON file: `%USERPROFILE%\.quadwrap\settings.json` (created lazily). Keys:
```
projection_strength, density, symmetry_blend, vertex_target, snap_offset,
preset, target_smooth_level, projection_mode, last_target
```
Presets: `PROP`, `CHARACTER`, `HARD_SURFACE` dicts overriding the above. `Save Current Settings` writes a named custom preset to JSON's `custom_presets`.

Sanitization clamps (like chandelier): floats to ranges, ints, valid modes. Always degrade gracefully.

## 7. Preview & commit

- **Preview**: create `qw_preview` duplicate of cage with shrinkWrap applied, set display override to reference/ghost (transparent-ish, wire+shaded). Re-running updates in place.
- **Commit**: snapshot world-space positions of the (projected) cage via `cmds.xform`-baked duplicate, delete history, assign standard Lambert, register in `COMMITTED`, remove preview + deformer node (keep non-destructive until commit). Single undo chunk. Update stats.
- **Undo Last**: if preview exists, delete it; else delete last committed cage. Track stack.
- **Clear All**: delete only tracked guides/ghosts/committed nodes + their own shaders. Never wildcard.

## 8. Heat map (deviation)

After commit (or on-demand button), color each vertex of the committed cage by its distance to the nearest point on the target surface, then optionally display CPV in viewport. Use `OpenMaya` (API 2 `MFnMesh`, `MMeshIntersector` or `closestPoint`). Color ramp: green <= 2mm, yellow mid, red >= clamp (slider). Implementation budget: O(cage_verts * target_faces); use a KD tree or `MMeshIntersector` for speed. Keep target clamp in settings.

## 9. Curvature-adaptive density (stretch goal)

- Sample guide strip polyline; compute turn angle between consecutive segments; where angle > threshold, subdivide that segment more.
- Apply resulting vertex counts to cage generation.
- If time runs short, ship a "density preview" that only colors high-curvature areas; do not block core.

## 10. Deformation test

- "Deform Test" on committed cage: create two joints (`qw_test_joint_a/b`) in plausible positions, `skinCluster`, rotate joint B by 45°, rebuild preview mesh (the skin deforms cage). Leave scene editable so user can inspect loop behavior. Register joints in committed group for cleanup.
- Should not crash if skeleton name exists; reuse/clean.

## 11. Stats & non-manifold

- Live readout in UI: cage poly count (`cmds.polyEvaluate`), guide count, non-manifold edge count. Non-manifold via `cmds.polyInfo(mesh, nonManifoldEdges=True)` if available; fallback to OpenMaya edge classification. If call uncertain, implement via `cmds.polyInfo` and parse; verify in Maya during week 1.
- Bad strips/self-intersections: try/except around duplicate/loft; set status label "flagged" not fatal.

## 12. Bad-selection / robustness rules (same habit as chandelier)

- `get_target_mesh()` returns shape/transform only if exactly one mesh transform; else `cmds.warning` + return None; UI buttons no-op with status.
- `cmds.objExists` before any op; wrap fragile ops in try/except reporting to UI label.
- Never use wildcard deletes; track created nodes.
- All long ops wrapped in chunk.

## 13. Three-week plan mapping

- **Oct 13 (week 1):** scaffolding, settings persistence + presets, selection handling, strip creation from vertex rows, registry, basic UI, non-manifold query verified. Strips draw and persist.
- **Oct 20 (week 2):** shrink-wrap driver wired to UI sliders; Auto-Ring + mirror; preview ghost; basic commit; stats. Fit works on simple mesh.
- **Oct 26 (week 3):** symmetry, presets, single-undo commit, heat map, deformation test, polish, demo asset. Curvature density only if week 2 finishes early.
- **Minimum shippable (Oct 27):** guided strip placement + shrink-wrap fit + presets + persisted settings + undo, even without heat map/deform test.

## 14. Resolved decisions (from user)

1. **Guide interaction model → `Select verts → Make strip`.** Strips created from selected vertex rows. Freehand curve deferred to stretch.
2. **Cage creation → `Fit provided cage`.** Tool = projection + helpers; no auto-meshing of arbitrary strips. Optional "Build Cage from Strips" remains week-3 stretch.
3. **Grading / submission → Both equally.** Balance robustness and visual impact; single script + README, not an installer.
4. **"Amazing" target → Heat map → deform test → curvature.** Curvature is week-3 stretch; heat map and deform test must ship.
5. **Symmetry.** Default mirror axis **X** (UI dropdown available if asset differs).
6. **Test asset → Not yet; will prepare.** Week 1 uses primitive proxy; real sculpt by Oct 20.

### No longer open
- Projection via Shrink Wrap deformer (Isaac) — accepted.
- polyRetopo benchmark — user to run on test sculpt; findings feed the demo narrative.

### Ready to proceed
On approval I start **Week-1 scope**: scaffolding, settings/presets, selection, strip creation, registry, basic UI, verified non-manifold query. Code written incrementally and tested through the Maya bridge.
