import os

from ..core.security import get_password_hash


def _default_users_enabled() -> bool:
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if environment == "production":
        return os.getenv("ALLOW_DEFAULT_SEED_USERS", "false").strip().lower() == "true"
    return True


def _seed_password() -> str:
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if environment == "production" and not _default_users_enabled():
        return "seed-users-disabled"
    password = os.getenv("DEFAULT_SEED_USER_PASSWORD", "password")
    if environment == "production" and password == "password":
        raise RuntimeError(
            "DEFAULT_SEED_USER_PASSWORD must be set to a non-default value when production seed users are enabled"
        )
    return password


_PASSWORD = _seed_password()


_INSECURE_DEVELOPMENT_USERS = [
    {
        "username": "superadmin",
        "email": "superadmin@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["superadmin"],
    },
    {
        "username": "orgadmin",
        "email": "orgadmin@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["orgadmin"],
        "organization_id": "org1",
        "projects": ["proj1"],
        "disabled": False,
    },
    {
        "username": "headcontract",
        "email": "headcontract@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["orguser"],
        "organization_id": "org1",
        "projects": ["proj1"],
        "disabled": False,
    },
    {
        "username": "contractmgr_org",
        "email": "contractmgr_org@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["orguser"],
        "organization_id": "org1",
        "projects": ["proj1"],
        "disabled": False,
    },
    {
        "username": "projectadmin",
        "email": "projectadmin@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["projectadmin"],
        "organization_id": "org2",
        "projects": ["proj2"],
        "disabled": False,
    },
    {
        "username": "contractmgr_proj",
        "email": "contractmgr_proj@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["projectuser"],
        "organization_id": "org2",
        "projects": ["proj2"],
        "disabled": False,
    },
    {
        "username": "doccontroller",
        "email": "doccontroller@example.com",
        "hashed_password": get_password_hash(_PASSWORD),
        "roles": ["projectuser"],
        "organization_id": "org2",
        "projects": ["proj2"],
        "disabled": False,
    },
]

DEFAULT_USERS = _INSECURE_DEVELOPMENT_USERS if _default_users_enabled() else []
