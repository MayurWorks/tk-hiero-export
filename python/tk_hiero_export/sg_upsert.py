# MIT License
#
# Copyright (c) 2026 SlateX
#
# Idempotent Version creation. Re-running an export (very common during
# editorial changes) must update the existing Version for a shot/plate, not
# pile up duplicates. Pure helpers, no hiero imports.

# Fields that identify a Version; never rewritten on an existing one.
_IDENTITY_FIELDS = ("code", "entity", "project", "user", "created_by")


def find_existing_version(sg, version_data):
    """The Version already created for this shot with this code, or None."""
    filters = [
        ["code", "is", version_data["code"]],
        ["entity", "is", version_data["entity"]],
    ]
    if version_data.get("project"):
        filters.append(["project", "is", version_data["project"]])
    return sg.find_one("Version", filters, ["id", "code"])


def upsert_version(sg, version_data):
    """
    Create the Version, or refresh the existing one with the same code on the
    same entity. Returns ``(version_dict, created)``.
    """
    existing = find_existing_version(sg, version_data)
    if existing is None:
        return sg.create("Version", version_data), True

    update = {k: v for k, v in version_data.items() if k not in _IDENTITY_FIELDS}
    if update:
        sg.update("Version", existing["id"], update)
    return {"type": "Version", "id": existing["id"], "code": existing["code"]}, False
