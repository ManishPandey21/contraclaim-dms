import logging
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

from fastapi import HTTPException
from pymongo import MongoClient
from bson import ObjectId
from bson.errors import InvalidId

logger = logging.getLogger(__name__)

@dataclass
class LinkValidationResult:
    """Result of document link validation"""
    valid_ids: List[ObjectId]
    documents: List[dict]
    invalid_ids: List[str]

class DocumentLinkingError(Exception):
    """Custom exception for document linking errors"""
    pass

class CircularReferenceError(DocumentLinkingError):
    """Exception raised when circular reference is detected"""
    pass

class DocumentNotFoundError(DocumentLinkingError):
    """Exception raised when document is not found"""
    pass

class DocumentLinkingService:
    """Service for managing document links with proper validation and error handling"""
    
    def __init__(self, db: MongoClient):
        if db is None:
            raise ValueError("Database connection cannot be None")
        self.db = db
        self.max_recursion_depth = 10  # Prevent infinite recursion
    
    def _validate_document_id(self, doc_id: str) -> ObjectId:
        """Validate and convert document ID to ObjectId"""
        if not doc_id or not isinstance(doc_id, str):
            raise ValueError(f"Invalid document ID: {doc_id}")
        
        try:
            return ObjectId(doc_id)
        except InvalidId as e:
            raise ValueError(f"Invalid ObjectId format: {doc_id}") from e
    
    def _validate_link_type(self, link_type: str) -> None:
        """Validate link type"""
        if not link_type or not isinstance(link_type, str):
            raise ValueError("Link type must be a non-empty string")
        
        # Define allowed link types (customize as needed)
        allowed_types = {
            'reference', 'related', 'supersedes', 'superseded_by',
            'amends', 'amended_by', 'responds_to', 'response_to'
        }
        
        if link_type not in allowed_types:
            logger.warning(f"Unknown link type: {link_type}")
    
    async def validate_document_ids(self, doc_ids: List[str]) -> LinkValidationResult:
        """
        Validate document IDs and return validation results.
        
        Args:
            doc_ids: List of document ID strings to validate
            
        Returns:
            LinkValidationResult with valid/invalid IDs and documents
        """
        if not doc_ids:
            return LinkValidationResult([], [], [])
        
        valid_ids = []
        documents = []
        invalid_ids = []
        
        for doc_id in doc_ids:
            try:
                object_id = self._validate_document_id(doc_id)
                
                # Check if document exists in database
                doc = await self.db.documents.find_one({"_id": object_id})
                if doc:
                    valid_ids.append(object_id)
                    documents.append(doc)
                else:
                    logger.warning(f"Document not found: {doc_id}")
                    invalid_ids.append(doc_id)
                    
            except (ValueError, InvalidId) as e:
                logger.error(f"Invalid document ID {doc_id}: {e}")
                invalid_ids.append(doc_id)
            except Exception as e:
                logger.error(f"Error validating document ID {doc_id}: {e}")
                invalid_ids.append(doc_id)
        
        logger.info(f"Validated {len(doc_ids)} IDs: {len(valid_ids)} valid, {len(invalid_ids)} invalid")
        return LinkValidationResult(valid_ids, documents, invalid_ids)
    
    async def check_circular_reference(self, source_id: str, target_id: str) -> bool:
        """
        Check if creating a link would result in circular reference.
        
        Args:
            source_id: Source document ID
            target_id: Target document ID
            
        Returns:
            True if circular reference would be created, False otherwise
        """
        if source_id == target_id:
            return True
        
        try:
            source_oid = self._validate_document_id(source_id)
            target_oid = self._validate_document_id(target_id)
            
            visited = set()
            return await self._check_references_recursive(str(target_oid), str(source_oid), visited, 0)
            
        except ValueError as e:
            logger.error(f"Invalid ID in circular reference check: {e}")
            raise DocumentLinkingError(f"Invalid document ID: {e}")
        except Exception as e:
            logger.error(f"Circular reference check failed: {e}")
            raise DocumentLinkingError(f"Circular reference check failed: {str(e)}")
    
    async def _check_references_recursive(
        self, 
        current_id: str, 
        target_id: str, 
        visited: Set[str], 
        depth: int
    ) -> bool:
        """Recursive helper for circular reference checking with depth limit"""
        if depth >= self.max_recursion_depth:
            logger.warning(f"Maximum recursion depth reached checking references")
            return False
        
        if current_id in visited:
            return False  # Already checked this path
        
        if current_id == target_id:
            return True  # Found circular reference
        
        visited.add(current_id)
        
        try:
            # Convert string ID back to ObjectId for database query
            current_oid = ObjectId(current_id)
            doc = await self.db.documents.find_one({"_id": current_oid})
            
            if not doc:
                return False
            
            references = doc.get("references", [])
            for ref in references:
                ref_id = ref.get("documentId")
                if ref_id:
                    if await self._check_references_recursive(str(ref_id), target_id, visited.copy(), depth + 1):
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error in recursive reference check: {e}")
            return False
    
    async def create_link(self, source_id: str, target_id: str, link_type: str) -> bool:
        """
        Create a link between two documents.
        
        Args:
            source_id: Source document ID
            target_id: Target document ID  
            link_type: Type of link to create
            
        Returns:
            True if link was created, False if link already exists
            
        Raises:
            HTTPException: If documents not found or circular reference detected
        """
        try:
            # Validate inputs
            source_oid = self._validate_document_id(source_id)
            target_oid = self._validate_document_id(target_id)
            self._validate_link_type(link_type)
            
            # Check if documents exist
            validation_result = await self.validate_document_ids([source_id, target_id])
            if validation_result.invalid_ids:
                raise HTTPException(
                    status_code=404,
                    detail=f"Documents not found: {validation_result.invalid_ids}"
                )
            
            # Check for existing link
            existing = await self.db.documents.find_one({
                "_id": source_oid,
                "references.documentId": str(target_oid)
            })
            
            if existing:
                logger.info(f"Link already exists between {source_id} and {target_id}")
                return False
            
            # Check for circular reference
            if await self.check_circular_reference(source_id, target_id):
                raise HTTPException(
                    status_code=400,
                    detail="Cannot create link: would result in circular reference"
                )
            
            # Create the link
            result = await self.db.documents.update_one(
                {"_id": source_oid},
                {
                    "$push": {
                        "references": {
                            "documentId": str(target_oid),
                            "linkType": link_type
                        }
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"Created {link_type} link from {source_id} to {target_id}")
                return True
            else:
                logger.warning(f"Failed to create link from {source_id} to {target_id}")
                return False
            
        except HTTPException:
            raise
        except DocumentLinkingError:
            raise
        except Exception as e:
            logger.error(f"Failed to create link: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create link: {str(e)}")
    
    async def get_all_linked_documents(self, doc_id: str, max_depth: int = 5) -> Set[str]:
        """
        Get all documents linked to the given document up to max_depth.
        
        Args:
            doc_id: Document ID to start from
            max_depth: Maximum traversal depth
            
        Returns:
            Set of linked document IDs
        """
        try:
            doc_oid = self._validate_document_id(doc_id)
            linked_docs = set()
            visited = set()
            
            await self._traverse_links_recursive(str(doc_oid), linked_docs, visited, 1, max_depth)
            
            logger.info(f"Found {len(linked_docs)} linked documents for {doc_id}")
            return linked_docs
            
        except ValueError as e:
            logger.error(f"Invalid document ID in get_all_linked_documents: {e}")
            raise DocumentLinkingError(f"Invalid document ID: {e}")
        except Exception as e:
            logger.error(f"Failed to get linked documents: {e}")
            raise DocumentLinkingError(f"Failed to get linked documents: {str(e)}")
    
    async def _traverse_links_recursive(
        self,
        current_id: str,
        linked_docs: Set[str],
        visited: Set[str],
        depth: int,
        max_depth: int
    ) -> None:
        """Recursive helper for traversing document links"""
        if depth > max_depth or current_id in visited:
            return
        
        visited.add(current_id)
        
        try:
            current_oid = ObjectId(current_id)
            doc = await self.db.documents.find_one({"_id": current_oid})
            
            if not doc:
                return
            
            references = doc.get("references", [])
            for ref in references:
                ref_id = ref.get("documentId")
                if ref_id and ref_id not in linked_docs:
                    linked_docs.add(ref_id)
                    await self._traverse_links_recursive(
                        ref_id, linked_docs, visited.copy(), depth + 1, max_depth
                    )
                    
        except Exception as e:
            logger.error(f"Error traversing links from {current_id}: {e}")
    
    async def bulk_link_documents(
        self,
        source_id: str,
        target_ids: List[str],
        link_type: str
    ) -> Tuple[List[str], List[str]]:
        """
        Create links from source document to multiple target documents.
        
        Args:
            source_id: Source document ID
            target_ids: List of target document IDs
            link_type: Type of links to create
            
        Returns:
            Tuple of (successfully_linked_ids, failed_ids)
        """
        if not target_ids:
            return [], []
        
        successfully_linked = []
        failed_links = []
        
        logger.info(f"Bulk linking {len(target_ids)} documents to {source_id}")
        
        for target_id in target_ids:
            try:
                success = await self.create_link(source_id, target_id, link_type)
                if success:
                    successfully_linked.append(target_id)
                else:
                    failed_links.append(target_id)
                    
            except HTTPException as e:
                logger.warning(f"Failed to link document {target_id}: {e.detail}")
                failed_links.append(target_id)
            except Exception as e:
                logger.error(f"Unexpected error linking document {target_id}: {e}")
                failed_links.append(target_id)
        
        logger.info(f"Bulk link complete: {len(successfully_linked)} successful, {len(failed_links)} failed")
        return successfully_linked, failed_links
    
    async def update_link_type(
        self,
        source_id: str,
        target_id: str,
        new_link_type: str
    ) -> bool:
        """
        Update the type of an existing link between documents.
        
        Args:
            source_id: Source document ID
            target_id: Target document ID
            new_link_type: New link type
            
        Returns:
            True if link was updated, False if link not found
        """
        try:
            source_oid = self._validate_document_id(source_id)
            target_oid = self._validate_document_id(target_id)
            self._validate_link_type(new_link_type)
            
            result = await self.db.documents.update_one(
                {
                    "_id": source_oid,
                    "references.documentId": str(target_oid)
                },
                {
                    "$set": {
                        "references.$.linkType": new_link_type
                    }
                }
            )
            
            success = result.modified_count > 0
            if success:
                logger.info(f"Updated link type from {source_id} to {target_id}: {new_link_type}")
            else:
                logger.warning(f"Link not found to update: {source_id} -> {target_id}")
            
            return success
            
        except ValueError as e:
            logger.error(f"Invalid ID in update_link_type: {e}")
            raise DocumentLinkingError(f"Invalid document ID: {e}")
        except Exception as e:
            logger.error(f"Failed to update link type: {e}")
            raise DocumentLinkingError(f"Failed to update link type: {str(e)}")
    
    async def remove_link(self, source_id: str, target_id: str) -> bool:
        """
        Remove a link between two documents.
        
        Args:
            source_id: Source document ID
            target_id: Target document ID
            
        Returns:
            True if link was removed, False if link not found
        """
        try:
            source_oid = self._validate_document_id(source_id)
            target_oid = self._validate_document_id(target_id)
            
            result = await self.db.documents.update_one(
                {"_id": source_oid},
                {
                    "$pull": {
                        "references": {"documentId": str(target_oid)}
                    }
                }
            )
            
            success = result.modified_count > 0
            if success:
                logger.info(f"Removed link from {source_id} to {target_id}")
            else:
                logger.warning(f"Link not found to remove: {source_id} -> {target_id}")
            
            return success
            
        except ValueError as e:
            logger.error(f"Invalid ID in remove_link: {e}")
            raise DocumentLinkingError(f"Invalid document ID: {e}")
        except Exception as e:
            logger.error(f"Failed to remove link: {e}")
            raise DocumentLinkingError(f"Failed to remove link: {str(e)}")
    
    async def get_document_links(self, doc_id: str) -> List[Dict[str, str]]:
        """
        Get all outgoing links from a document.
        
        Args:
            doc_id: Document ID
            
        Returns:
            List of link dictionaries with documentId and linkType
        """
        try:
            doc_oid = self._validate_document_id(doc_id)
            
            doc = await self.db.documents.find_one({"_id": doc_oid})
            if not doc:
                raise DocumentNotFoundError(f"Document not found: {doc_id}")
            
            links = doc.get("references", [])
            logger.info(f"Retrieved {len(links)} links for document {doc_id}")
            return links
            
        except ValueError as e:
            logger.error(f"Invalid document ID in get_document_links: {e}")
            raise DocumentLinkingError(f"Invalid document ID: {e}")
        except DocumentNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to get document links: {e}")
            raise DocumentLinkingError(f"Failed to get document links: {str(e)}")

# Factory function
def create_document_linking_service(db: MongoClient) -> DocumentLinkingService:
    """Create document linking service instance"""
    return DocumentLinkingService(db)
