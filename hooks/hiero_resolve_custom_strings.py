# Copyright (c) 2014 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.
import sgtk
from tank import Hook


class HieroResolveCustomStrings(Hook):
    """
    This class implements a hook that is used to resolve custom tokens into
    their concrete value when paths are being processed during the export.
    """

    # Cache of shots that have already been pulled from shotgun
    _sg_lookup_cache = {}

    def execute(self, task, keyword, **kwargs):
        """
        The default implementation of the custom resolver simply looks up
        the keyword from the Shotgun Shot entity dictionary. For example,
        to pull the shot code, you would simply specify 'code'. To pull
        the sequence code you would use 'sg_sequence.Sequence.code'.

        :param task: The export task being processed.
        :param str keyword: The keyword token that needs to be resolved.

        :returns: The resolved keyword value to be replaced into the
            associated string.
        :rtype: str
        """
        shot_code = task._item.name()

        if keyword == "{Projectcode}":
            return self._project_code()

        # grab the shot from the cache, or the get_shot hook if not cached
        sg_shot = self._sg_lookup_cache.get(shot_code)
        if sg_shot is None:
            fields = [
                ctf["keyword"]
                for ctf in self.parent.get_setting("custom_template_fields")
            ]
            sg_shot = self.parent.execute_hook(
                "hook_get_shot",
                task=task,
                item=task._item,
                data=self.parent.preprocess_data,
                fields=fields,
                upload_thumbnail=False,
            )

            self._sg_lookup_cache[shot_code] = sg_shot

        if sg_shot is None:
            raise RuntimeError(
                "Could not find shot for custom resolver: %s" % keyword
            )

        # strip off the leading and trailing curly brackets
        keyword = keyword[1:-1]
        result = sg_shot.get(keyword, "")

        self.parent.log_debug(
            "Custom resolver: %s[%s] -> %s" % (shot_code, keyword, result)
        )

        return result

    # sg_projectcode per project id, so the template is not re-queried for
    # every task and keyword.
    _project_code_cache = {}

    def _project_code(self):
        project = self.parent.context.project
        if project is None:
            raise sgtk.TankError("Cannot resolve {Projectcode}: no project in the context.")

        cached = self._project_code_cache.get(project["id"])
        if cached:
            return cached

        record = self.parent.shotgun.find_one(
            "Project", [["id", "is", project["id"]]], ["sg_projectcode"]
        )
        code = (record or {}).get("sg_projectcode")
        if not code:
            raise sgtk.TankError(
                "Project '%s' has an empty sg_projectcode in Flow Production Tracking; "
                "paths would contain 'None'. Fill it in before exporting." % project.get("name")
            )
        self._project_code_cache[project["id"]] = code
        return code
