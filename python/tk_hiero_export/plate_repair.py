# MIT License
#
# Copyright (c) 2026 SlateX
#
# "Repair" = bring ShotGrid in line with what is already on disk:
#   * register EXR plates that were copied but never published
#   * fix Version names that were case-mangled (Str_scep_1_strm_e1_0070_...)
#     so they match the file they belong to
# The plan is computed first (nothing is written), shown to the user, and only
# then applied. Pure helpers: tk / sg / sgtk are injected, no hiero imports.

import os
from collections import namedtuple

try:
    from . import plate_publish as pp
except ImportError:  # loaded by file path (backfill script, unit tests)
    import plate_publish as pp

PlateItem = namedtuple("PlateItem", "shot shot_id version pattern frames complete")
VersionRename = namedtuple("VersionRename", "version_id old new")
RepairPlan = namedtuple("RepairPlan", "plates renames")


def plate_versions(tk, template_name, fields):
    """
    ``{version: printf_pattern}`` for every plate version found on disk for one
    Shot, using the plate template so folder naming is never duplicated here.
    """
    template = tk.templates[template_name]
    found = tk.paths_from_template(
        template, fields, skip_keys=["version", "SEQ"], skip_missing_optional_keys=True
    )
    versions = {}
    for path in found:
        path = path.replace("\\", "/")
        flds = template.get_fields(path)
        version = flds.get("version")
        if version is None:
            continue
        pattern_fields = dict(flds)
        pattern_fields["SEQ"] = "%04d"
        versions[int(version)] = template.apply_fields(pattern_fields)
    return versions


def plan_plates(tk, sg, project_name, published_file_type, template_names, shot_filter=None):
    """List the on-disk plates that have no PublishedFile yet."""
    project = sg.find_one("Project", [["name", "is", project_name]], ["sg_projectcode"])
    if not project:
        raise ValueError("Project %r not found" % project_name)

    filters = [["project", "is", {"type": "Project", "id": project["id"]}]]
    if shot_filter:
        filters.append(["code", "in", list(shot_filter)])
    shots = sg.find("Shot", filters, ["code", "sg_sequence"])

    items = []
    for shot in shots:
        sequence = (shot.get("sg_sequence") or {}).get("name")
        if not sequence:
            continue
        fields = {
            "Sequence": sequence,
            "Shot": shot["code"],
            "Projectcode": project.get("sg_projectcode"),
            "fileext": "exr",
        }
        seen = set()
        for template_name in template_names:
            if template_name not in tk.templates:
                continue
            for version, pattern in sorted(plate_versions(tk, template_name, fields).items()):
                if version in seen:
                    continue
                seen.add(version)
                entity = {"type": "Shot", "id": shot["id"]}
                if pp.find_existing_publish(
                    sg, entity, os.path.basename(pattern), version, published_file_type
                ):
                    continue
                pat, padding = pp.to_printf_pattern(pattern)
                first, last, frames = pp.scan_sequence(pat, padding)
                complete = bool(frames) and not pp.missing_frames(first, last, frames) \
                    and not pp.zero_byte_frames(pat, frames)
                items.append(PlateItem(shot["code"], shot["id"], version, pattern,
                                       len(frames), complete))
    return items


def plan_version_renames(sg, project):
    """
    Hiero reference Versions whose code differs from their file name only by
    letter case (an old ``.capitalize()`` bug). Anything else is left alone.
    """
    versions = sg.find(
        "Version",
        [["project", "is", project], ["code", "contains", "_reference_p"]],
        ["code", "published_files"],
    )
    renames = []
    for version in versions:
        for published in version.get("published_files") or []:
            stem = os.path.splitext(published.get("name") or "")[0]
            code = version["code"]
            if stem and stem != code and stem.lower() == code.lower():
                renames.append(VersionRename(version["id"], code, stem))
                break
    return renames


def build_plan(tk, sg, project, template_names, published_file_type):
    plates = plan_plates(tk, sg, project["name"], published_file_type, template_names)
    return RepairPlan(plates, plan_version_renames(sg, project))


def is_empty(plan):
    return not plan.plates and not plan.renames


def format_plan(plan, limit=12):
    lines = []
    ok = [p for p in plan.plates if p.complete]
    bad = [p for p in plan.plates if not p.complete]
    if ok:
        lines.append("Plates to register (%d):" % len(ok))
        lines += ["  %s p%03d - %d frames" % (p.shot, p.version, p.frames) for p in ok[:limit]]
        if len(ok) > limit:
            lines.append("  ... and %d more" % (len(ok) - limit))
    if bad:
        lines.append("Incomplete plates that will be SKIPPED (%d):" % len(bad))
        lines += ["  %s p%03d - %d frames (gaps or empty files)" % (p.shot, p.version, p.frames)
                  for p in bad[:limit]]
    if plan.renames:
        lines.append("Versions to rename (%d):" % len(plan.renames))
        lines += ["  %s  ->  %s" % (r.old, r.new) for r in plan.renames[:limit]]
        if len(plan.renames) > limit:
            lines.append("  ... and %d more" % (len(plan.renames) - limit))
    return "\n".join(lines) if lines else "Nothing to repair."


def apply_plan(tk, sg, sgtk_module, plan, published_file_type, logger=None):
    """Apply ``plan``. Returns (registered, skipped, renamed)."""
    registered = skipped = renamed = 0
    for item in plan.plates:
        if not item.complete:
            skipped += 1
            continue
        ctx = tk.context_from_entity("Shot", item.shot_id)
        result = pp.register_plate_publish(
            sgtk_module, tk, sg, ctx, item.pattern, item.version,
            published_file_type, logger=logger,
        )
        if result:
            registered += 1
        else:
            skipped += 1
    for rename in plan.renames:
        sg.update("Version", rename.version_id, {"code": rename.new})
        renamed += 1
    return registered, skipped, renamed
