"""Config-defined contexts (``terminal`` and friends): pick a choice, run its command."""

from .picker import Choice, detached, pick


class ContextError(Exception):
    pass


class Context:
    def __init__(self, name: str, cfg: dict):
        section = cfg.get("contexts", {}).get(name)
        if section is None:
            raise ContextError(f"unknown context '{name}'")
        self.name = name
        self.title = section.get("title", f"Open {name}")
        self.choices = [Choice(**c) for c in section.get("choices", [])]

    def open(self, args: list[str]) -> None:
        if len(self.choices) == 1:
            chosen = self.choices[0].id
        else:
            chosen = pick(self.title, " ".join(args), self.choices)
        command = next((c.command for c in self.choices if c.id == chosen), None)
        if command:  # None: cancelled
            # Extra arguments are appended to the choice's command line.
            detached(["bash", "-c", command + ' "$@"', "omaprofile", *args])
