"""The one place identity comes from (bug #8).

There is no login in this demo: the UI's "Acting as" switcher sends a user
*key*, and the server looks the person and role up here. A name or role sent
by a client is never trusted for authorisation. In production this module
would read the verified identity from SSO (OIDC claims) instead of a table.
"""

from trustagent.domain import Approver, Role

DEMO_USERS: dict[str, Approver] = {
    "thandi": Approver(name="Thandi Nkosi", role=Role.FINANCE_ANALYST),
    "sipho": Approver(name="Sipho Dlamini", role=Role.FINANCE_MANAGER),
    "lerato": Approver(name="Lerato Khumalo", role=Role.DEPARTMENT_HEAD),
}


class UnknownUser(LookupError):
    """Not a known user (HTTP 401)."""


def resolve_acting_as(user_key: str | None) -> Approver:
    user = DEMO_USERS.get((user_key or "").strip().lower())
    if user is None:
        raise UnknownUser("Unknown user. Choose who you are acting as.")
    return user
