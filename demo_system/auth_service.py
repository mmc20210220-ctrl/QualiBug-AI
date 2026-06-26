from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any
import itertools


@dataclass
class User:
    username: str
    password: str
    role: str = "user"
    status: str = "active"
    failed_attempts: int = 0


class AuthService:
    """A tiny in-memory auth service used by generated tests.

    It simulates business rules:
    1. Active user with correct password can log in.
    2. Five wrong passwords lock the account.
    3. Locked account cannot log in.
    4. Normal user cannot access admin page.
    """

    def __init__(self):
        self._seq = itertools.count(1)
        self.users: Dict[str, User] = {}

    def create_user(self, role: str = "user", status: str = "active", password: str = "Passw0rd!") -> Dict[str, Any]:
        username = f"{role}_{status}_{next(self._seq)}"
        user = User(username=username, password=password, role=role, status=status)
        self.users[username] = user
        return {
            "username": username,
            "password": password,
            "role": role,
            "status": status,
        }

    def login(self, username: str, password: str) -> Dict[str, Any]:
        user = self.users.get(username)
        if user is None:
            return {"success": False, "error_code": "USER_NOT_FOUND"}

        if user.status == "locked":
            return {"success": False, "error_code": "ACCOUNT_LOCKED"}

        if password != user.password:
            user.failed_attempts += 1
            if user.failed_attempts >= 5:
                user.status = "locked"
                return {"success": False, "error_code": "ACCOUNT_LOCKED"}
            return {"success": False, "error_code": "INVALID_PASSWORD"}

        user.failed_attempts = 0
        return {"success": True, "redirect_url": "/home", "role": user.role}

    def can_access(self, username: str, resource: str) -> bool:
        user = self.users.get(username)
        if not user or user.status != "active":
            return False
        normalized_resource = str(resource).strip().lower()
        is_admin_resource = normalized_resource in {"admin_page", "admin", "/admin", "/admin/", "administrator"} or "admin" in normalized_resource
        if is_admin_resource and user.role != "admin":
            return False
        return True
