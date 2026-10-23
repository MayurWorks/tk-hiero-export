#!/usr/bin/env python3
"""
Register already-ingested EXR plates as PublishedFiles ("Plate" type).

New ingests are registered automatically by the PTR Copy Plate exporter. This
script covers shots ingested before that existed.

It runs outside Nuke/Hiero using the project's pipeline configuration:

    python backfill_plate_publishes.py \\
        --config /path/to/pipeline_configuration \\
        --project storm            # dry run: only lists what it would register

    python backfill_plate_publishes.py ... --apply    # really register

Plates ingested before the file-name change live under the template
"hiero_copy_path_legacy"; the script checks both templates by default.

Credentials come from the environment (script user):
    SG_SITE_URL, SG_SCRIPT_NAME, SG_SCRIPT_KEY

Safe to run repeatedly: a plate version that already has a PublishedFile of the
same name/version/type is skipped.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.abspath(os.path.join(HERE, os.pardir, "python", "tk_hiero_export"))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

import plate_publish as _plate_publish  # noqa: E402  (no hiero imports)
import plate_repair  # noqa: E402

# One implementation shared with the in-Hiero "Repair Plates & Versions" command.
plate_versions = plate_repair.plate_versions


def _load_plate_publish():
    return _plate_publish


def backfill(tk, sg, project_name, published_file_type="Plate",
             template_name="hiero_copy_path", shot_filter=None, dry_run=True,
             sgtk_module=None, log=print):
    pp = _load_plate_publish()
    project = sg.find_one("Project", [["name", "is", project_name]], ["sg_projectcode"])
    if not project:
        raise SystemExit("Project %r not found" % project_name)

    filters = [["project", "is", {"type": "Project", "id": project["id"]}]]
    if shot_filter:
        filters.append(["code", "in", list(shot_filter)])
    shots = sg.find("Shot", filters, ["code", "sg_sequence"])

    registered = skipped = missing = 0
    for shot in shots:
        seq = (shot.get("sg_sequence") or {}).get("name")
        if not seq:
            log("SKIP %s: no sequence" % shot["code"])
            skipped += 1
            continue
        fields = {
            "Sequence": seq,
            "Shot": shot["code"],
            "Projectcode": project.get("sg_projectcode"),
            "fileext": "exr",
        }
        versions = plate_versions(tk, template_name, fields)
        if not versions:
            missing += 1
            continue
        ctx = tk.context_from_entity("Shot", shot["id"])
        for version, pattern in sorted(versions.items()):
            first, last, frames = pp.scan_sequence(*(pp.to_printf_pattern(pattern)))
            log("%s p%03d: %s (%s frames)" % (shot["code"], version, pattern, len(frames)))
            if dry_run:
                continue
            res = pp.register_plate_publish(
                sgtk_module, tk, sg, ctx, pattern, version, published_file_type
            )
            if res:
                registered += 1
            else:
                skipped += 1
    log("done: registered=%d skipped=%d shots-without-plates=%d%s"
        % (registered, skipped, missing, " (dry run)" if dry_run else ""))
    return registered, skipped, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", required=True, help="pipeline configuration root (contains install/core)")
    ap.add_argument("--project", required=True, help="ShotGrid project name, e.g. storm")
    ap.add_argument("--type", default="Plate", help="PublishedFileType name (default Plate)")
    ap.add_argument("--shots", nargs="*", help="limit to these shot codes")
    ap.add_argument("--apply", action="store_true", help="really register (default is dry run)")
    ap.add_argument(
        "--template", nargs="+", default=["hiero_copy_path", "hiero_copy_path_legacy"],
        help="plate path template(s) to scan (default: current and legacy naming)",
    )
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(args.config, "install", "core", "python"))
    import sgtk  # noqa: E402

    user = sgtk.authentication.ShotgunAuthenticator().create_script_user(
        api_script=os.environ["SG_SCRIPT_NAME"],
        api_key=os.environ["SG_SCRIPT_KEY"],
        host=os.environ["SG_SITE_URL"],
    )
    sgtk.set_authenticated_user(user)
    tk = sgtk.sgtk_from_path(args.config)
    for template_name in args.template:
        if template_name not in tk.templates:
            print("template %r is not in this configuration, skipping" % template_name)
            continue
        print("== template %s ==" % template_name)
        backfill(tk, tk.shotgun, args.project, args.type, template_name=template_name,
                 shot_filter=args.shots, dry_run=not args.apply, sgtk_module=sgtk)


if __name__ == "__main__":
    main()
