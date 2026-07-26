from typing import Optional

from pymongo.database import Database
import jwt
from jwt import PyJWTError as JWTError
from bcrypt import checkpw
from datetime import timedelta, datetime
from ..models.user import User
from ..core.config import Settings

settings = Settings()
ALGORITHM = "HS256"


class AuthService:
    def __init__(self, db: Database):
        self.db = db

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verify a plain password against its bcrypt hashed version.
        hashed_password can be stored as a string or bytes, ensure correct encoding.
        """
        if isinstance(hashed_password, str):
            hashed_password_bytes = hashed_password.encode("utf-8")
        else:
            hashed_password_bytes = hashed_password
        return checkpw(plain_password.encode("utf-8"), hashed_password_bytes)

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a JWT access token with an expiry.
        Defaults to 15 minutes expiry if expires_delta is omitted.
        """
        to_encode = data.copy()
        expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=15))
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt

    def get_current_user(self, token: str) -> Optional[User]:
        """
        Decode JWT token to get user info.
        Returns None if token invalid/expired or user not found.
        """
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
            email: Optional[str] = payload.get("sub")
            if not email:
                return None
        except JWTError as err:
            # Optional: log error here
            return None

        # Assuming synchronous pymongo usage; if async database, this should be awaited.
        user_data = self.db.users.find_one({"email": email})
        if not user_data:
            return None
        return User(**user_data)
