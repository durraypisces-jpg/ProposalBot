"""Users, roles and row-level access (FR-01, FR-09).

Demo users from the semantic layer stand in for single sign-on. In production,
`user_from_claims` maps the identity provider's token claims (e.g. Microsoft
Entra ID groups) to the same User object, and nothing downstream changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .semantic import Role, SemanticLayer


@dataclass(frozen=True)
class User:
    username: str
    name: str
    role: Role
    sites: tuple[str, ...] = field(default_factory=tuple)
    entities: tuple[str, ...] = field(default_factory=tuple)
    # Which client (and so which client database) the user belongs to. Comes
    # from the login token only; text in the chat can never change it.
    client_id: str = "demo-client"

    def scope_values(self, scope: str) -> tuple[str, ...]:
        """Values this user is limited to for a row filter scope ("sites" or "entities")."""
        return {"sites": self.sites, "entities": self.entities}[scope]

    def describe_scope(self) -> str:
        parts = [f"Role: {self.role.label}"]
        if "site_id" in self.role.row_filters:
            parts.append("Sites: " + ", ".join(self.sites))
        if "entity_id" in self.role.row_filters:
            parts.append("Entities: " + ", ".join(self.entities))
        return "; ".join(parts)


class UnknownUser(LookupError):
    pass


def user_from_claims(layer: SemanticLayer, claims: dict) -> User:
    role_name = claims["role"]
    if role_name not in layer.roles:
        raise UnknownUser(f"Unknown role {role_name!r}")
    if claims.get("client_id") and claims["client_id"] not in layer.clients:
        raise UnknownUser(f"Unknown client {claims['client_id']!r}")
    return User(
        username=claims["username"],
        name=claims.get("name", claims["username"]),
        role=layer.roles[role_name],
        sites=tuple(claims.get("sites", ())),
        entities=tuple(claims.get("entities", ())),
        client_id=claims.get("client_id") or layer.default_client,
    )


def demo_user(layer: SemanticLayer, username: str) -> User:
    for claims in layer.demo_users:
        if claims["username"] == username:
            return user_from_claims(layer, claims)
    raise UnknownUser(f"Unknown user {username!r}")
