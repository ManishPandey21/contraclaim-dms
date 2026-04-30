import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from pymongo.database import Database
from bson.objectid import ObjectId
from bson.errors import InvalidId
from bcrypt import gensalt, hashpw, checkpw

from ..models.user import User

logger = logging.getLogger(__name__)

class UserServiceError(Exception):
    """Custom exception for user service errors"""
    pass

class UserNotFoundError(UserServiceError):
    """Exception raised when user is not found"""
    pass

class UserAlreadyExistsError(UserServiceError):
    """Exception raised when user already exists"""
    pass

class InvalidUserIdError(UserServiceError):
    """Exception raised when user ID is invalid"""
    pass

class UserService:
    """Async service for user CRUD operations with proper error handling"""
    
    def __init__(self, db: Database):
        if db is None:
            raise ValueError("Database connection cannot be None")
        self.db = db
    
    def _validate_user_id(self, user_id: str) -> ObjectId:
        """
        Validate and convert user ID to ObjectId.
        
        Args:
            user_id: User ID string to validate
            
        Returns:
            ObjectId instance
            
        Raises:
            InvalidUserIdError: If ID format is invalid
        """
        if not user_id or not isinstance(user_id, str):
            raise InvalidUserIdError(f"Invalid user ID: {user_id}")
        
        try:
            return ObjectId(user_id)
        except InvalidId as e:
            raise InvalidUserIdError(f"Invalid ObjectId format: {user_id}") from e
    
    def _validate_pagination(self, skip: int, limit: int) -> None:
        """Validate pagination parameters"""
        if not isinstance(skip, int) or skip < 0:
            raise ValueError("Skip must be a non-negative integer")
        
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("Limit must be a positive integer")
        
        if limit > 1000:  # Prevent excessive loads
            raise ValueError("Limit cannot exceed 1000")
    
    def _validate_email(self, email: str) -> None:
        """Basic email validation"""
        if not email or not isinstance(email, str):
            raise ValueError("Email must be a non-empty string")
        
        if "@" not in email or "." not in email:
            raise ValueError("Invalid email format")
    
    def _hash_password(self, password: str) -> str:
        """
        Hash password using bcrypt.
        
        Args:
            password: Plain text password
            
        Returns:
            Hashed password string
        """
        if not password or not isinstance(password, str):
            raise ValueError("Password must be a non-empty string")
        
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")
        
        salt = gensalt()
        hashed = hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    def _verify_password(self, password: str, hashed_password: str) -> bool:
        """
        Verify password against hash.
        
        Args:
            password: Plain text password
            hashed_password: Hashed password from database
            
        Returns:
            True if password matches, False otherwise
        """
        try:
            if isinstance(hashed_password, str):
                hashed_bytes = hashed_password.encode('utf-8')
            else:
                hashed_bytes = hashed_password
            
            return checkpw(password.encode('utf-8'), hashed_bytes)
        except Exception as e:
            logger.error(f"Password verification failed: {e}")
            return False
    
    async def get_users(self, skip: int = 0, limit: int = 50) -> List[Optional[User]]:
        """
        Retrieve multiple users with pagination.
        
        Args:
            skip: Number of users to skip
            limit: Maximum number of users to return
            
        Returns:
            List of User instances
            
        Raises:
            UserServiceError: If retrieval fails
        """
        try:
            self._validate_pagination(skip, limit)
            
            logger.info(f"Retrieving users: skip={skip}, limit={limit}")
            
            cursor = self.db.users.find().skip(skip).limit(limit)
            users = []
            
            async for user_data in cursor:
                try:
                    if user_data:
                        # Remove sensitive fields before creating User object
                        safe_user_data = {k: v for k, v in user_data.items() if k != 'passwordHash'}
                        user = User(**safe_user_data)
                        users.append(user)
                except Exception as e:
                    logger.warning(f"Failed to parse user {user_data.get('_id', 'unknown')}: {e}")
                    users.append(None)  # Keep position but mark as failed
            
            logger.info(f"Retrieved {len(users)} users")
            return users
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve users: {e}")
            raise UserServiceError(f"User retrieval failed: {str(e)}")
    
    async def get_user(self, user_id: str) -> Optional[User]:
        """
        Retrieve a single user by ID.
        
        Args:
            user_id: User ID string
            
        Returns:
            User instance if found, None otherwise
            
        Raises:
            UserServiceError: If retrieval fails
            InvalidUserIdError: If user ID is invalid
        """
        try:
            user_oid = self._validate_user_id(user_id)
            
            logger.debug(f"Retrieving user: {user_id}")
            
            user_data = await self.db.users.find_one({"_id": user_oid})
            
            if not user_data:
                logger.info(f"User not found: {user_id}")
                return None
            
            # Remove sensitive fields
            safe_user_data = {k: v for k, v in user_data.items() if k != 'passwordHash'}
            user = User(**safe_user_data)
            logger.info(f"Retrieved user: {user_id}")
            return user
            
        except InvalidUserIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve user {user_id}: {e}")
            raise UserServiceError(f"User retrieval failed: {str(e)}")
    
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """
        Retrieve a user by email address.
        
        Args:
            email: Email address
            
        Returns:
            User instance if found, None otherwise
            
        Raises:
            UserServiceError: If retrieval fails
        """
        try:
            self._validate_email(email)
            
            logger.debug(f"Retrieving user by email: {email}")
            
            user_data = await self.db.users.find_one({"email": email})
            
            if not user_data:
                logger.info(f"User not found by email: {email}")
                return None
            
            # Remove sensitive fields
            safe_user_data = {k: v for k, v in user_data.items() if k != 'passwordHash'}
            user = User(**safe_user_data)
            logger.info(f"Retrieved user by email: {email}")
            return user
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve user by email {email}: {e}")
            raise UserServiceError(f"User retrieval by email failed: {str(e)}")
    
    async def create_user(self, user: Any, password: str) -> User:
        """
        Create a new user.
        
        Args:
            user: User instance to create
            password: Plain text password
            
        Returns:
            Created user without sensitive fields
            
        Raises:
            UserServiceError: If creation fails
            UserAlreadyExistsError: If user already exists
        """
        try:
            if user is None:
                raise ValueError("user is required")

            if not password:
                raise ValueError("password is required")

            # Accept either a full User model, a create payload, or a dict
            if isinstance(user, User):
                user_payload = user.model_dump(by_alias=True, exclude_unset=True)
            elif hasattr(user, "model_dump"):
                user_payload = user.model_dump(by_alias=True, exclude_unset=True)
            elif isinstance(user, dict):
                user_payload = dict(user)
            else:
                raise ValueError("user must be a User, UserCreate, or dict")

            self._validate_email(user_payload.get("email"))

            logger.info(f"Creating new user: {user_payload.get('email')}")

            # Check if user already exists
            existing_user = await self.db.users.find_one({"email": user_payload.get("email")})
            if existing_user:
                raise UserAlreadyExistsError(f"User with email {user_payload.get('email')} already exists")

            # Hash password
            hashed_password = self._hash_password(password)

            # Convert to dict and add hashed password
            user_dict = dict(user_payload)
            user_dict["passwordHash"] = hashed_password
            # Keep a normalized hashed_password field for downstream readers
            user_dict["hashed_password"] = hashed_password

            # Remove _id if present to let MongoDB generate it
            user_dict.pop('_id', None)

            result = await self.db.users.insert_one(user_dict)

            if not result.inserted_id:
                raise UserServiceError("Failed to insert user - no ID returned")

            # Retrieve the created user (without password)
            created_user_data = await self.db.users.find_one({"_id": result.inserted_id})

            if not created_user_data:
                raise UserServiceError("Failed to retrieve created user")

            # Remove sensitive fields
            safe_user_data = {k: v for k, v in created_user_data.items() if k != 'passwordHash'}
            # Normalize identifiers for Pydantic model (expects strings)
            if "_id" in safe_user_data:
                safe_user_data["_id"] = str(safe_user_data["_id"])
            if "hashed_password" not in safe_user_data:
                safe_user_data["hashed_password"] = hashed_password
            created_user = User(**safe_user_data)

            logger.info(f"Created user: {result.inserted_id}")
            return created_user

        except (ValueError, UserAlreadyExistsError):
            raise
        except UserServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            raise UserServiceError(f"User creation failed: {str(e)}")
    
    async def update_user(self, user_id: str, updated_user) -> Optional[User]:
        """
        Update an existing user.

        Accepts either:
          - a full/partial Pydantic model with model_dump()
          - a plain dict of fields to update
        
        Args:
            user_id: User ID string
            updated_user: Partial user data (Pydantic model or dict)
            
        Returns:
            Updated user if successful, None if user not found
            
        Raises:
            UserServiceError: If update fails
            InvalidUserIdError: If user ID is invalid
        """
        try:
            user_oid = self._validate_user_id(user_id)

            logger.info(f"Updating user: {user_id}")

            # Normalize input to dict
            update_dict: Dict[str, Any]
            if hasattr(updated_user, "model_dump"):
                update_dict = updated_user.model_dump(
                    by_alias=True,
                    exclude_unset=True,
                    exclude={'_id', 'id'}
                )
            elif isinstance(updated_user, dict):
                update_dict = dict(updated_user)
            else:
                raise ValueError("updated_user must be a dict or a Pydantic model")

            # Clean/normalize fields
            update_dict.pop('_id', None)
            update_dict.pop('id', None)
            # Never store plain password
            update_dict.pop('password', None)
            # Ensure DB uses 'passwordHash'
            if 'hashed_password' in update_dict:
                update_dict['passwordHash'] = update_dict.pop('hashed_password')

            # Validate email if being updated
            if 'email' in update_dict:
                self._validate_email(update_dict['email'])
                # Check if email is already taken by another user
                existing_user = await self.db.users.find_one({
                    "email": update_dict['email'],
                    "_id": {"$ne": user_oid}
                })
                if existing_user:
                    raise UserAlreadyExistsError(
                        f"Email {update_dict['email']} is already taken by another user"
                    )

            if not update_dict:
                logger.warning(f"No fields to update for user: {user_id}")
                return await self.get_user(user_id)

            result = await self.db.users.update_one(
                {"_id": user_oid},
                {"$set": update_dict}
            )

            if result.matched_count == 0:
                logger.info(f"User not found for update: {user_id}")
                return None

            if result.modified_count == 0:
                logger.info(f"User not modified (no changes): {user_id}")
            else:
                logger.info(f"Updated user: {user_id}")

            # Return the updated user (lightweight object to avoid strict Pydantic validation)
            return await self.get_user_by_id(user_id)

        except InvalidUserIdError:
            raise
        except (ValueError, UserAlreadyExistsError):
            raise
        except UserServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update user {user_id}: {e}")
            raise UserServiceError(f"User update failed: {str(e)}")
    
    async def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        """
        Change user password after verifying old password.
        
        Args:
            user_id: User ID string
            old_password: Current password
            new_password: New password
            
        Returns:
            True if password was changed successfully
            
        Raises:
            UserServiceError: If password change fails
            InvalidUserIdError: If user ID is invalid
        """
        try:
            user_oid = self._validate_user_id(user_id)
            
            if not old_password or not new_password:
                raise ValueError("Both old and new passwords are required")
            
            logger.info(f"Changing password for user: {user_id}")
            
            # Get user with password hash
            user_data = await self.db.users.find_one({"_id": user_oid})
            if not user_data:
                raise UserNotFoundError(f"User not found: {user_id}")
            
            # Verify old password
            stored_hash = user_data.get('passwordHash')
            if not stored_hash:
                raise UserServiceError("User has no password set")
            
            if not self._verify_password(old_password, stored_hash):
                raise ValueError("Current password is incorrect")
            
            # Hash new password
            new_hashed_password = self._hash_password(new_password)
            
            # Update password
            result = await self.db.users.update_one(
                {"_id": user_oid},
                {"$set": {"passwordHash": new_hashed_password}}
            )
            
            success = result.modified_count > 0
            if success:
                logger.info(f"Password changed successfully for user: {user_id}")
            else:
                logger.warning(f"Password change failed for user: {user_id}")
            
            return success
            
        except InvalidUserIdError:
            raise
        except (ValueError, UserNotFoundError):
            raise
        except Exception as e:
            logger.error(f"Failed to change password for user {user_id}: {e}")
            raise UserServiceError(f"Password change failed: {str(e)}")
    
    async def check_user_dependencies(self, user_id: str) -> List[str]:
        """
        Check whether a user has dependent records that should block deletion.

        Currently checks a minimal set of collections and returns their names
        when at least one matching record exists. This keeps the router logic
        compatible without failing when new collections are added later.
        """
        try:
            user_oid = self._validate_user_id(user_id)
            dependencies: List[str] = []
            # Helper queries both ObjectId and string representations
            oid_or_str = {"$in": [user_oid, str(user_oid), user_id]}

            try:
                doc_count = await self.db.documents.count_documents(
                    {"created_by": oid_or_str}, limit=1
                )
                if doc_count:
                    dependencies.append("documents")
            except Exception as e:
                logger.debug(f"Dependency check failed for documents ({user_id}): {e}")

            try:
                task_count = await self.db.tasks.count_documents(
                    {"assigned_to": oid_or_str}, limit=1
                )
                if task_count:
                    dependencies.append("tasks")
            except Exception as e:
                logger.debug(f"Dependency check failed for tasks ({user_id}): {e}")

            return dependencies
        except InvalidUserIdError:
            raise
        except Exception as e:
            logger.warning(f"check_user_dependencies failed for {user_id}: {e}")
            return []
    
    async def delete_user(self, user_id: str) -> bool:
        """
        Delete a user by ID.
        
        Args:
            user_id: User ID string
            
        Returns:
            True if user was deleted, False if not found
            
        Raises:
            UserServiceError: If deletion fails
            InvalidUserIdError: If user ID is invalid
        """
        try:
            user_oid = self._validate_user_id(user_id)
            
            logger.info(f"Deleting user: {user_id}")
            
            result = await self.db.users.delete_one({"_id": user_oid})
            
            success = result.deleted_count > 0
            if success:
                logger.info(f"Deleted user: {user_id}")
            else:
                logger.info(f"User not found for deletion: {user_id}")
            
            return success
            
        except InvalidUserIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete user {user_id}: {e}")
            raise UserServiceError(f"User deletion failed: {str(e)}")
    
    async def authenticate_user(self, email: str, password: str) -> Optional[User]:
        """
        Authenticate user with email and password.
        
        Args:
            email: User email
            password: Plain text password
            
        Returns:
            User instance if authentication successful, None otherwise
            
        Raises:
            UserServiceError: If authentication process fails
        """
        try:
            self._validate_email(email)
            
            if not password:
                raise ValueError("Password is required")
            
            logger.debug(f"Authenticating user: {email}")
            
            # Get user with password hash
            user_data = await self.db.users.find_one({"email": email})
            if not user_data:
                logger.info(f"Authentication failed - user not found: {email}")
                return None
            
            # Verify password
            stored_hash = user_data.get('passwordHash')
            if not stored_hash:
                logger.warning(f"User {email} has no password set")
                return None
            
            if not self._verify_password(password, stored_hash):
                logger.info(f"Authentication failed - invalid password: {email}")
                return None
            
            # Remove sensitive fields
            safe_user_data = {k: v for k, v in user_data.items() if k != 'passwordHash'}
            user = User(**safe_user_data)
            
            logger.info(f"Authentication successful: {email}")
            return user
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Authentication failed for {email}: {e}")
            raise UserServiceError(f"Authentication failed: {str(e)}")
    
    async def user_exists(self, user_id: str) -> bool:
        """
        Check if a user exists.
        
        Args:
            user_id: User ID string
            
        Returns:
            True if user exists, False otherwise
            
        Raises:
            UserServiceError: If check fails
            InvalidUserIdError: If user ID is invalid
        """
        try:
            user_oid = self._validate_user_id(user_id)
            
            count = await self.db.users.count_documents({"_id": user_oid}, limit=1)
            return count > 0
            
        except InvalidUserIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to check user existence {user_id}: {e}")
            raise UserServiceError(f"User existence check failed: {str(e)}")
    
    def _to_light_user(self, doc: Dict[str, Any]):
        """
        Build a lightweight user-like object with attributes expected by routers.
        Avoid strict Pydantic validation to tolerate partial seed data.
        """
        class _U:
            pass
        u = _U()
        u.id = str(doc.get("_id") or doc.get("id") or "")
        u.email = doc.get("email")
        u.username = doc.get("username") or (u.email.split("@")[0] if u.email else "")
        u.roles = doc.get("roles", [])
        u.disabled = bool(doc.get("disabled", False))
        # Support both hashed_password (new) and passwordHash (older seeds)
        u.hashed_password = doc.get("hashed_password") or doc.get("passwordHash") or ""
        u.organization_id = doc.get("organization_id")
        # Preserve profile metadata so profile routes can read/write them
        u.first_name = doc.get("first_name") or doc.get("firstName")
        u.last_name = doc.get("last_name") or doc.get("lastName")
        u.job_title = doc.get("job_title") or doc.get("jobTitle")
        u.projects = doc.get("projects", [])
        u.preferences = doc.get("preferences", {})
        u.created_at = doc.get("created_at")
        u.last_login = doc.get("last_login")
        return u

    async def get_user_by_email_secure(self, email: str) -> Optional[Any]:
        """
        Returns a permissive lightweight user object for login flow.
        """
        try:
            self._validate_email(email)
            doc = await self.db.users.find_one({"email": email})
            if not doc:
                return None
            return self._to_light_user(doc)
        except Exception as e:
            logger.error(f"get_user_by_email_secure failed for {email}: {e}")
            return None

    async def update_last_login(self, user_id: str) -> None:
        """
        Update last_login timestamp and increment login_count.
        """
        try:
            oid = self._validate_user_id(user_id)
            await self.db.users.update_one(
                {"_id": oid},
                {
                    "$set": {"last_login": datetime.utcnow()},
                    "$inc": {"login_count": 1},
                },
            )
        except Exception as e:
            logger.warning(f"update_last_login failed for {user_id}: {e}")

    async def get_user_by_id(self, user_id: str) -> Optional[Any]:
        """
        Fetch user by id and return lightweight user object.
        """
        try:
            oid = self._validate_user_id(user_id)
            doc = await self.db.users.find_one({"_id": oid})
            if not doc:
                return None
            return self._to_light_user(doc)
        except InvalidUserIdError:
            return None
        except Exception as e:
            logger.error(f"get_user_by_id failed for {user_id}: {e}")
            return None

    async def get_organization_name(self, organization_id: str) -> Optional[str]:
        """
        Resolve organization name from organizations collection.
        """
        try:
            query: Dict[str, Any]
            try:
                query = {"_id": ObjectId(str(organization_id))}
            except Exception:
                query = {"$or": [{"_id": str(organization_id)}, {"id": str(organization_id)}]}
            doc = await self.db.organizations.find_one(query)
            return (doc or {}).get("name")
        except Exception as e:
            logger.warning(f"get_organization_name failed for {organization_id}: {e}")
            return None

    async def get_project_names(self, project_ids: List[str]) -> List[str]:
        """
        Resolve list of project names for provided IDs.
        """
        try:
            if not project_ids:
                return []
            obj_ids: List[ObjectId] = []
            str_ids: List[str] = []
            for pid in project_ids:
                try:
                    obj_ids.append(ObjectId(str(pid)))
                except Exception:
                    str_ids.append(str(pid))
            or_filters: List[Dict[str, Any]] = []
            if obj_ids:
                or_filters.append({"_id": {"$in": obj_ids}})
            if str_ids:
                or_filters.append({"id": {"$in": str_ids}})
            if not or_filters:
                return []
            docs = await self.db.projects.find({"$or": or_filters}).to_list(length=None)
            return [d.get("name") for d in docs if d and d.get("name")]
        except Exception as e:
            logger.warning(f"get_project_names failed: {e}")
            return []

    async def get_users_paginated(self, query: Dict[str, Any], pagination: Dict[str, int]):
        """
        Return (users, total_count) honoring provided query and pagination.
        """
        try:
            skip = max(0, int(pagination.get("skip", 0)))
            limit = max(1, int(pagination.get("limit", 50)))
            cursor = self.db.users.find(query or {}).skip(skip).limit(limit)
            users: List[Any] = []
            async for doc in cursor:
                users.append(self._to_light_user(doc))
            total = await self.db.users.count_documents(query or {})
            return users, total
        except Exception as e:
            logger.error(f"get_users_paginated failed: {e}")
            return [], 0

    async def check_user_exists(
        self,
        email: Optional[str],
        username: Optional[str],
        exclude_user_id: Optional[str] = None
    ) -> bool:
        """
        Check if a user with given email or username exists (optionally excluding one user).
        """
        try:
            ors: List[Dict[str, Any]] = []
            if email:
                ors.append({"email": email})
            if username:
                ors.append({"username": username})
            if not ors:
                return False
            q: Dict[str, Any] = {"$or": ors}
            if exclude_user_id:
                try:
                    q["_id"] = {"$ne": self._validate_user_id(exclude_user_id)}
                except InvalidUserIdError:
                    pass
            count = await self.db.users.count_documents(q, limit=1)
            return count > 0
        except Exception as e:
            logger.error(f"check_user_exists failed: {e}")
            return False

    async def get_users_count(self, filter_dict: Optional[Dict[str, Any]] = None) -> int:
        """
        Get count of users matching filter.
        
        Args:
            filter_dict: Optional filter dictionary
            
        Returns:
            Number of matching users
            
        Raises:
            UserServiceError: If count fails
        """
        try:
            filter_dict = filter_dict or {}
            count = await self.db.users.count_documents(filter_dict)
            logger.debug(f"User count: {count}")
            return count
            
        except Exception as e:
            logger.error(f"Failed to count users: {e}")
            raise UserServiceError(f"User count failed: {str(e)}")

# Factory function
def create_user_service(db: Database) -> UserService:
    """Create user service instance"""
    return UserService(db)
