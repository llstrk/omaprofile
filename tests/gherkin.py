"""A small Gherkin runner on the standard library, so the specs need no dependencies.

Supports Feature, Rule, Background (feature and rule level), Scenario, tags,
Given/When/Then/And/But and single- or multi-column data tables. Step
definitions register with ``@step("text with {placeholders}")``; a placeholder
matches a quoted string's contents or a bare word.

Each scenario becomes a unittest test (``load_tests`` in test_features.py), and
``python3 tests/gherkin.py [features/]`` prints every step with its result.
Scenarios tagged @nest are skipped: they are checked by eye in an omadev nest.
"""

import re
import sys
import traceback
import unittest
from dataclasses import dataclass, field
from pathlib import Path

SKIP_TAGS = {"@nest": "checked by eye in an omadev nest"}


@dataclass
class Step:
    keyword: str  # Given / When / Then (And and But resolved to the one before)
    text: str
    line: int
    table: list[list[str]] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    line: int
    tags: list[str]
    steps: list[Step]
    background: list[Step]
    rule: str = ""


@dataclass
class Feature:
    name: str
    path: Path
    scenarios: list[Scenario]


KEYWORDS = ("Given", "When", "Then", "And", "But")


def parse(path: Path) -> Feature:
    feature_name, scenarios = "", []
    feature_bg: list[Step] = []
    rule_bg: list[Step] = []
    rule = ""
    target: list[Step] | None = None  # where steps go: a background or the current scenario
    tags: list[str] = []
    last_keyword = ""
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            tags += line.split()
            continue
        head, _, rest = line.partition(":")
        if head == "Feature":
            feature_name, target = rest.strip(), None
        elif head == "Rule":
            rule, rule_bg, target = rest.strip(), [], None
        elif head == "Background":
            target = rule_bg if rule else feature_bg
        elif head in ("Scenario", "Example"):
            sc = Scenario(rest.strip(), n, tags, [], [*feature_bg, *rule_bg], rule)
            scenarios.append(sc)
            target, tags = sc.steps, []
        elif line.startswith("|"):
            if target is None or not target:
                raise SyntaxError(f"{path}:{n}: table without a step")
            target[-1].table.append([c.strip() for c in line.strip("|").split("|")])
        elif line.split(" ", 1)[0] in KEYWORDS:
            if target is None:
                raise SyntaxError(f"{path}:{n}: step outside a scenario or background")
            word, text = line.split(" ", 1)
            keyword = last_keyword if word in ("And", "But") else word
            if not keyword:
                raise SyntaxError(f"{path}:{n}: {word} with no step before it")
            last_keyword = keyword
            target.append(Step(keyword, text.strip(), n))
        # anything else is free description text
    return Feature(feature_name, path, scenarios)


# ------------------------------------------------------------------ steps

STEPS: list[tuple[re.Pattern, object]] = []


def step(pattern: str):
    """Register a step definition. ``{name}`` matches "a quoted string" or a bare word."""
    regex = ""
    for part in re.split(r"(\{\w+\})", pattern):
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            regex += rf'(?:"(?P<{name}>[^"]*)"|(?P<{name}_bare>\S+))'
        else:
            regex += re.escape(part)
    compiled = re.compile(regex + r"\Z")

    def register(fn):
        STEPS.append((compiled, fn))
        return fn
    return register


class Undefined(Exception):
    pass


def find(text: str):
    for regex, fn in STEPS:
        m = regex.match(text)
        if m:
            args = {}
            for key, value in m.groupdict().items():
                if value is None:
                    continue
                args[key.removesuffix("_bare")] = value
            return fn, args
    raise Undefined(f"no step definition matches: {text}")


def run_scenario(scenario: Scenario, make_context, report=None) -> None:
    """Run the background and the steps in order; raise on the first failure."""
    ctx = make_context()
    failed = None
    try:
        for st in [*scenario.background, *scenario.steps]:
            if failed is not None:
                report and report(st, "skipped", None)
                continue
            try:
                fn, args = find(st.text)
                ctx.step = st
                fn(ctx, **args)
                report and report(st, "passed", None)
            except Exception as e:  # noqa: BLE001 - reported per step
                failed = (st, e)
                report and report(st, "undefined" if isinstance(e, Undefined) else "failed", e)
    finally:
        close = getattr(ctx, "close", None)
        if close:
            close()
    if failed:
        st, e = failed
        raise AssertionError(f"line {st.line}: {st.keyword} {st.text}\n  {type(e).__name__}: {e}") from e


def skip_reason(scenario: Scenario) -> str:
    return next((SKIP_TAGS[t] for t in scenario.tags if t in SKIP_TAGS), "")


# ------------------------------------------------------------------ unittest

def test_suite(feature_dir: Path, make_context) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for path in sorted(feature_dir.glob("*.feature")):
        feature = parse(path)
        attrs = {}
        for sc in feature.scenarios:
            def test(self, sc=sc):
                reason = skip_reason(sc)
                if reason:
                    self.skipTest(reason)
                run_scenario(sc, make_context)
            name = "test_" + re.sub(r"\W+", "_", sc.name.lower()).strip("_")
            test.__doc__ = sc.name
            attrs[name] = test
        cls = type(re.sub(r"\W+", "", feature.name.title()) or path.stem, (unittest.TestCase,), attrs)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    return suite


# ------------------------------------------------------------------ printing runner

def main(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    import sandbox  # noqa: F401  - before omaprofile is imported anywhere
    import steps_updates  # noqa: F401  - registers the steps
    from steps_updates import Context

    feature_dir = Path(argv[0]) if argv else here.parent / "features"
    color = sys.stdout.isatty()
    paint = {"passed": "32", "failed": "31", "undefined": "33", "skipped": "90"}
    mark = {"passed": "✓", "failed": "✗", "undefined": "?", "skipped": "-"}
    totals = {"passed": 0, "failed": 0, "skipped": 0}

    def out(text, status):
        print(f"\033[{paint[status]}m{text}\033[0m" if color else text)

    for path in sorted(feature_dir.glob("*.feature")):
        feature = parse(path)
        print(f"Feature: {feature.name}\n")
        rule = None
        for sc in feature.scenarios:
            if sc.rule != rule:
                rule = sc.rule
                print(f"  Rule: {rule}\n")
            reason = skip_reason(sc)
            tags = " ".join(sc.tags)
            print(f"    {tags + chr(10) + '    ' if tags else ''}Scenario: {sc.name}")
            if reason:
                for st in sc.steps:
                    out(f"      - {st.keyword} {st.text}", "skipped")
                out(f"      ({reason})\n", "skipped")
                totals["skipped"] += 1
                continue

            def report(st, status, error):
                if st in sc.background:
                    if status == "passed":
                        return
                out(f"      {mark[status]} {st.keyword} {st.text}", status)
                if error is not None:
                    detail = str(error) if not isinstance(error, AssertionError) else str(error)
                    for line in (detail or traceback.format_exception_only(error)[-1]).splitlines()[:6]:
                        out(f"          {line}", status)
            try:
                run_scenario(sc, Context, report)
                totals["passed"] += 1
            except AssertionError:
                totals["failed"] += 1
            print()
    print(f"{sum(totals.values())} scenarios: {totals['passed']} passed, "
          f"{totals['failed']} failed, {totals['skipped']} skipped")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    # Run the importable copy, the one the step definitions register with.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gherkin
    sys.exit(gherkin.main(sys.argv[1:]))
