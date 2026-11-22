"""One JSON file per collection in a data directory."""

import json
import os


class JsonStore:
    def __init__(self, directory):
        self.directory = directory

    def path(self, name):
        return os.path.join(self.directory, f"{name}.json")

    def read(self, name):
        """Return the list in ``<name>.json``; a missing file is an empty list."""
        try:
            with open(self.path(name)) as handle:
                return json.load(handle)
        except FileNotFoundError:
            return []

    def write(self, name, items):
        """Replace ``<name>.json``. A reader never sees a half-written file."""
        os.makedirs(self.directory, exist_ok=True)
        temporary = self.path(name) + ".tmp"
        with open(temporary, "w") as handle:
            json.dump(items, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, self.path(name))
