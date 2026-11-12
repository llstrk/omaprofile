"""Config-defined contexts (``terminal`` and friends): pick a choice, run its command."""

from dataclasses import fields

from .picker import Choice, detached, pick

CHOICE_KEYS = {f.name for f in fields(Choice)}


class ContextError(Exception):
    pass


class Context:
    def __init__(self, name: str, cfg: dict):
        section = cfg.get("contexts", {}).get(name)
        if section is None:
            raise ContextError(f"unknown context '{name}'")
        if not isinstance(section, dict) or not isinstance(section.get("choices", []), list):
            raise ContextError(f"context '{name}' must be an object with a 'choices' list")
        self.name = name
        self.title = section.get("title", f"Open {name}")
        self.choices = [self._choice(c) for c in section.get("choices", [])]

    def _choice(self, spec) -> Choice:
        where = f"context '{self.name}'"
        if not isinstance(spec, dict) or not spec.get("id") or not spec.get("command"):
            raise ContextError(f"{where}: every choice needs an 'id' and a 'command' (got {spec!r})")
        unknown = sorted(set(spec) - CHOICE_KEYS)
        if unknown:
            allowed = ", ".join(sorted(CHOICE_KEYS))
            raise ContextError(f"{where}, choice '{spec['id']}': unknown key(s) {', '.join(unknown)} "
                               f"(allowed: {allowed})")
        return Choice(**{k: str(v) for k, v in spec.items()})

    def open(self, args: list[str]) -> None:
        if len(self.choices) == 1:
            chosen = self.choices[0].id
        else:
            chosen = pick(self.title, " ".join(args), self.choices)
        command = next((c.command for c in self.choices if c.id == chosen), None)
        if command:  # None: cancelled
            # Extra arguments are appended to the choice's command line.
            detached(["bash", "-c", command + ' "$@"', "omaprofile", *args])
