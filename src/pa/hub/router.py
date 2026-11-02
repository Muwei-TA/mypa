import fnmatch

from pa.config.schema import Rule
from pa.ingress.base import Event


def matches(rule: Rule, event: Event) -> bool:
    for field, expected in rule.match.model_dump(exclude_none=True).items():
        if field == "label":
            labels = event.payload.get("labels", [])
            if expected not in labels and expected != event.payload.get("label"):
                return False
            continue
        actual = getattr(event, field, None)
        if actual is None:
            actual = event.payload.get(field)
        options = expected if isinstance(expected, list) else [expected]
        if actual is None or not any(fnmatch.fnmatchcase(str(actual), str(o)) for o in options):
            return False
    return True
